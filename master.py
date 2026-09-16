import os
import time
import telebot
from pymongo import MongoClient
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- रेंडर को चकमा देने के लिए डमी सर्वर ---
def keep_alive():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Master is Live!")
    
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

keep_alive()
# -----------------------------------------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

# आपका असली टेलीग्राम आईडी 
OWNER_ID = 6069200310 

bot = telebot.TeleBot(BOT_TOKEN)
db = MongoClient(MONGO_URI)["novel_db"]
queue = db["jobs"]
users_db = db["users"]

def is_approved(chat_id):
    if chat_id == OWNER_ID:
        return True
    user = users_db.find_one({"_id": chat_id})
    if user and user.get("status") == "approved":
        if user.get("expires_at", 0) > time.time():
            return True
        else:
            users_db.update_one({"_id": chat_id}, {"$set": {"status": "expired"}})
    return False

@bot.message_handler(commands=['start'])
def send_welcome(message):
    chat_id = message.chat.id
    if is_approved(chat_id):
        bot.reply_to(message, "👋 स्वागत है! मुझे अनुवाद के लिए कोई भी .txt फाइल भेजें।")
    else:
        bot.reply_to(message, f"🔒 यह एक निजी बॉट है। कृपया पहुंच प्राप्त करने के लिए /request का उपयोग करें।\nआपका आईडी: <code>{chat_id}</code>", parse_mode="HTML")

@bot.message_handler(commands=['request'])
def request_access(message):
    chat_id = message.chat.id
    if chat_id == OWNER_ID:
        return bot.reply_to(message, "आप इस बॉट के मालिक हैं!")
        
    user = users_db.find_one({"_id": chat_id})
    if user and user.get("status") == "approved" and user.get("expires_at", 0) > time.time():
        return bot.reply_to(message, "✅ आपके पास पहले से ही पहुंच है! आप फाइल भेज सकते हैं।")
        
    users_db.update_one(
        {"_id": chat_id}, 
        {"$set": {"status": "pending", "name": message.from_user.first_name}},
        upsert=True
    )
    bot.reply_to(message, "⏳ आपका अनुरोध मालिक को भेज दिया गया है। कृपया उनके निर्णय की प्रतीक्षा करें।")
    bot.send_message(OWNER_ID, f"🙋 नया अनुरोध!\nनाम: {message.from_user.first_name}\nआईडी: <code>{chat_id}</code>\n\nअनुमति देने के लिए नीचे दिए गए कमांड का उपयोग करें:\n/approve {chat_id} 30", parse_mode="HTML")

@bot.message_handler(commands=['approve'])
def approve_user(message):
    if message.chat.id != OWNER_ID:
        return
    
    try:
        parts = message.text.split()
        target_id = int(parts[1])
        days = int(parts[2]) if len(parts) > 2 else 30
        
        expires_at = time.time() + (days * 86400)
        users_db.update_one(
            {"_id": target_id},
            {"$set": {"status": "approved", "expires_at": expires_at}},
            upsert=True
        )
        bot.reply_to(message, f"✅ उपयोगकर्ता {target_id} को {days} दिनों के लिए अनुमति दे दी गई है।")
        bot.send_message(target_id, "🎉 बधाई हो! आपके अनुरोध को स्वीकृति मिल गई है। अब आप अनुवाद के लिए फाइल भेज सकते हैं।")
    except Exception:
        bot.reply_to(message, "⚠️ सही तरीका: /approve <आईडी> <दिन>\nउदाहरण: /approve 123456789 30")

@bot.message_handler(commands=['reject'])
def reject_user(message):
    if message.chat.id != OWNER_ID:
        return
    
    try:
        target_id = int(message.text.split()[1])
        users_db.update_one({"_id": target_id}, {"$set": {"status": "rejected"}})
        bot.reply_to(message, f"❌ उपयोगकर्ता {target_id} का अनुरोध अस्वीकार कर दिया गया है।")
        bot.send_message(target_id, "❌ आपके अनुरोध को अस्वीकार कर दिया गया है।")
    except Exception:
        bot.reply_to(message, "⚠️ सही तरीका: /reject <आईडी>")

@bot.message_handler(content_types=['document'])
def handle_docs(message):
    if not is_approved(message.chat.id):
        return bot.reply_to(message, "🔒 आपके पास इस बॉट का उपयोग करने की अनुमति नहीं है। कृपया /request भेजें।")
        
    try:
        file_info = bot.get_file(message.document.file_id)
        
        if message.document.file_size > 20 * 1024 * 1024:
            return bot.reply_to(message, "❌ फाइल २० एमबी से बड़ी है।")
            
        if not message.document.file_name.endswith('.txt'):
            return bot.reply_to(message, "❌ कृपया केवल .txt फाइलें ही भेजें।")

        job = {
            "chat_id": message.chat.id,
            "file_path": file_info.file_path,
            "file_name": message.document.file_name,
            "status": "queued",
            "timestamp": time.time()
        }
        queue.insert_one(job)
        
        bot.reply_to(message, "✅ आपकी फाइल कतार में लग गई है!\nवर्कर सर्वर इसे जल्द ही अनुवाद करके भेज देगा।")
        
    except Exception as e:
        bot.reply_to(message, f"⚠️ त्रुटि: {e}")

print("मास्टर सर्वर चालू हो गया है... (संदेशों की प्रतीक्षा में)")
bot.infinity_polling()
