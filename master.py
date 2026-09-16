import os
import time
import telebot
from pymongo import MongoClient

# Environment Variables से चाबियां उठाना
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

bot = telebot.TeleBot(BOT_TOKEN)
db = MongoClient(MONGO_URI)["novel_db"]
queue = db["jobs"]

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "👋 हेलो! मुझे कोई भी .txt फाइल भेजें (Max 20MB) और मैं उसे हिंदी में अनुवाद कर दूँगा।\n\n(यह बॉट कई सर्वर्स पर एक साथ काम कर सकता है!)")

@bot.message_handler(content_types=['document'])
def handle_docs(message):
    try:
        file_info = bot.get_file(message.document.file_id)
        
        # 20MB Limit Check
        if message.document.file_size > 20 * 1024 * 1024:
            return bot.reply_to(message, "❌ फाइल 20MB से बड़ी है। टेलीग्राम बॉट API केवल 20MB तक की फाइलें सपोर्ट करता है।")
            
        if not message.document.file_name.endswith('.txt'):
            return bot.reply_to(message, "❌ अभी के लिए कृपया केवल .txt फाइलें ही भेजें।")

        # काम को डेटाबेस (Queue) में डालना
        job = {
            "chat_id": message.chat.id,
            "file_path": file_info.file_path,
            "file_name": message.document.file_name,
            "status": "queued",
            "timestamp": time.time()
        }
        queue.insert_one(job)
        
        bot.reply_to(message, "✅ आपकी फाइल कतार (Queue) में लग गई है!\nकोई एक वर्कर सर्वर इसे जल्द ही अनुवाद करके भेज देगा।")
        
    except Exception as e:
        bot.reply_to(message, f"⚠️ एरर: {e}")

print("Master Server Started... (Listening for messages)")
bot.infinity_polling()
