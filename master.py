import os
import time
import logging
import telebot
from pymongo import MongoClient
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [MASTER] %(levelname)s: %(message)s")
log = logging.getLogger("master")


# --- रेंडर को चकमा देने के लिए डमी सर्वर ---
def keep_alive():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Master is Live!")

        def log_message(self, *args):
            pass

    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


keep_alive()
# -----------------------------------------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

# आपका असली टेलीग्राम आईडी
OWNER_ID = int(os.getenv("OWNER_ID", 6069200310))
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", 20))
MAX_ACTIVE_JOBS_PER_USER = int(os.getenv("MAX_ACTIVE_JOBS_PER_USER", 3))

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
db = MongoClient(MONGO_URI)["novel_db"]
queue = db["jobs"]
users_db = db["users"]

STATUS_ICON = {"queued": "⏳", "running": "🔄", "done": "✅", "failed": "❌", "cancelled": "🚫"}


# ------------------------- helpers -------------------------
def is_owner(chat_id):
    return chat_id == OWNER_ID


def is_approved(chat_id):
    if is_owner(chat_id):
        return True
    user = users_db.find_one({"_id": chat_id})
    if user and user.get("status") == "approved":
        if user.get("expires_at", 0) > time.time():
            return True
        users_db.update_one({"_id": chat_id}, {"$set": {"status": "expired"}})
    return False


def days_left(user):
    return max(0, int((user.get("expires_at", 0) - time.time()) // 86400))


def fmt_time(ts):
    return time.strftime("%d-%m-%Y %H:%M", time.localtime(ts)) if ts else "-"


def queue_position(job_id):
    """1-based position of a queued job (running jobs count ahead of it)."""
    job = queue.find_one({"_id": job_id})
    if not job or job["status"] != "queued":
        return None
    ahead = queue.count_documents({"status": "queued", "timestamp": {"$lt": job["timestamp"]}})
    running = queue.count_documents({"status": "running"})
    return ahead + running + 1


# ------------------------- user commands -------------------------
@bot.message_handler(commands=['start'])
def send_welcome(message):
    chat_id = message.chat.id
    if is_approved(chat_id):
        bot.reply_to(message, "👋 स्वागत है! मुझे अनुवाद के लिए कोई भी .txt फाइल भेजें।\n\nसभी कमांड देखने के लिए /help भेजें।")
    else:
        bot.reply_to(message, f"🔒 यह एक निजी बॉट है। कृपया पहुंच प्राप्त करने के लिए /request का उपयोग करें।\nआपका आईडी: <code>{chat_id}</code>")


@bot.message_handler(commands=['help'])
def send_help(message):
    text = (
        "📖 <b>उपलब्ध कमांड</b>\n\n"
        "/start - बॉट शुरू करें\n"
        "/request - पहुंच का अनुरोध करें\n"
        "/status - अपने कामों की स्थिति देखें\n"
        "/cancel - कतार में लगा आखिरी काम रद्द करें\n"
        "/me - अपनी सदस्यता की जानकारी\n"
        "/help - यह संदेश\n\n"
        "📄 अनुवाद के लिए बस .txt फाइल भेजें (अधिकतम {mb} MB)।"
    ).format(mb=MAX_FILE_MB)
    if is_owner(message.chat.id):
        text += (
            "\n\n👑 <b>मालिक कमांड</b>\n"
            "/approve &lt;id&gt; [दिन] - उपयोगकर्ता को अनुमति दें\n"
            "/reject &lt;id&gt; - अनुरोध अस्वीकार करें\n"
            "/revoke &lt;id&gt; - पहुंच वापस लें\n"
            "/users - सभी उपयोगकर्ता\n"
            "/pending - लंबित अनुरोध\n"
            "/stats - कतार के आंकड़े\n"
            "/broadcast &lt;संदेश&gt; - सभी स्वीकृत उपयोगकर्ताओं को संदेश\n"
            "/clearfailed - विफल काम हटाएं"
        )
    bot.reply_to(message, text)


@bot.message_handler(commands=['request'])
def request_access(message):
    chat_id = message.chat.id
    if is_owner(chat_id):
        return bot.reply_to(message, "आप इस बॉट के मालिक हैं!")

    user = users_db.find_one({"_id": chat_id})
    if user and user.get("status") == "approved" and user.get("expires_at", 0) > time.time():
        return bot.reply_to(message, "✅ आपके पास पहले से ही पहुंच है! आप फाइल भेज सकते हैं।")
    if user and user.get("status") == "pending":
        return bot.reply_to(message, "⏳ आपका अनुरोध पहले से लंबित है। कृपया प्रतीक्षा करें।")

    users_db.update_one(
        {"_id": chat_id},
        {"$set": {"status": "pending", "name": message.from_user.first_name,
                  "username": message.from_user.username, "requested_at": time.time()}},
        upsert=True
    )
    bot.reply_to(message, "⏳ आपका अनुरोध मालिक को भेज दिया गया है। कृपया उनके निर्णय की प्रतीक्षा करें।")
    uname = f"@{message.from_user.username}" if message.from_user.username else "-"
    bot.send_message(OWNER_ID,
                     f"🙋 <b>नया अनुरोध!</b>\nनाम: {message.from_user.first_name}\nयूज़रनेम: {uname}\nआईडी: <code>{chat_id}</code>\n\n"
                     f"/approve {chat_id} 30\n/reject {chat_id}")


@bot.message_handler(commands=['me'])
def my_info(message):
    chat_id = message.chat.id
    if is_owner(chat_id):
        return bot.reply_to(message, "👑 आप मालिक हैं — असीमित पहुंच।")
    user = users_db.find_one({"_id": chat_id})
    if not user:
        return bot.reply_to(message, "आपका कोई रिकॉर्ड नहीं है। /request भेजें।")
    status = user.get("status")
    if status == "approved" and user.get("expires_at", 0) > time.time():
        bot.reply_to(message, f"✅ स्थिति: स्वीकृत\n📅 समाप्ति: {fmt_time(user['expires_at'])}\n⏳ बचे दिन: {days_left(user)}")
    else:
        bot.reply_to(message, f"स्थिति: <b>{status}</b>\nपहुंच के लिए /request भेजें।")


@bot.message_handler(commands=['status'])
def job_status(message):
    chat_id = message.chat.id
    jobs = list(queue.find({"chat_id": chat_id}).sort("timestamp", -1).limit(5))
    if not jobs:
        return bot.reply_to(message, "आपने अभी तक कोई फाइल नहीं भेजी है।")
    lines = ["📋 <b>आपके हाल के काम</b>\n"]
    for j in jobs:
        icon = STATUS_ICON.get(j["status"], "•")
        line = f"{icon} <b>{j['file_name']}</b> — {j['status']}"
        if j["status"] == "queued":
            pos = queue_position(j["_id"])
            line += f" (कतार में #{pos})"
        elif j["status"] == "running":
            line += f" ({j.get('progress', 0)}%)"
        elif j["status"] == "failed":
            line += f"\n   ↳ {str(j.get('error', ''))[:80]}"
        lines.append(line)
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=['cancel'])
def cancel_job(message):
    chat_id = message.chat.id
    job = queue.find_one({"chat_id": chat_id, "status": "queued"}, sort=[("timestamp", -1)])
    if not job:
        return bot.reply_to(message, "कतार में आपका कोई काम नहीं है जिसे रद्द किया जा सके।")
    queue.update_one({"_id": job["_id"], "status": "queued"}, {"$set": {"status": "cancelled"}})
    bot.reply_to(message, f"🚫 <b>{job['file_name']}</b> रद्द कर दिया गया।")


# ------------------------- owner commands -------------------------
def owner_only(func):
    def wrapper(message):
        if not is_owner(message.chat.id):
            return
        return func(message)
    return wrapper


@bot.message_handler(commands=['approve'])
@owner_only
def approve_user(message):
    try:
        parts = message.text.split()
        target_id = int(parts[1])
        days = int(parts[2]) if len(parts) > 2 else 30
        expires_at = time.time() + (days * 86400)
        users_db.update_one(
            {"_id": target_id},
            {"$set": {"status": "approved", "expires_at": expires_at, "approved_at": time.time()}},
            upsert=True
        )
        bot.reply_to(message, f"✅ उपयोगकर्ता {target_id} को {days} दिनों के लिए अनुमति दे दी गई है।")
        try:
            bot.send_message(target_id, f"🎉 बधाई हो! आपके अनुरोध को स्वीकृति मिल गई है ({days} दिन)। अब आप अनुवाद के लिए फाइल भेज सकते हैं।")
        except Exception as e:
            bot.reply_to(message, f"(उपयोगकर्ता को सूचित नहीं कर सके: {e})")
    except Exception:
        bot.reply_to(message, "⚠️ सही तरीका: /approve &lt;आईडी&gt; &lt;दिन&gt;\nउदाहरण: /approve 123456789 30")


@bot.message_handler(commands=['reject'])
@owner_only
def reject_user(message):
    try:
        target_id = int(message.text.split()[1])
        users_db.update_one({"_id": target_id}, {"$set": {"status": "rejected"}})
        bot.reply_to(message, f"❌ उपयोगकर्ता {target_id} का अनुरोध अस्वीकार कर दिया गया है।")
        try:
            bot.send_message(target_id, "❌ आपके अनुरोध को अस्वीकार कर दिया गया है।")
        except Exception:
            pass
    except Exception:
        bot.reply_to(message, "⚠️ सही तरीका: /reject &lt;आईडी&gt;")


@bot.message_handler(commands=['revoke'])
@owner_only
def revoke_user(message):
    try:
        target_id = int(message.text.split()[1])
        users_db.update_one({"_id": target_id}, {"$set": {"status": "revoked", "expires_at": 0}})
        bot.reply_to(message, f"🚫 उपयोगकर्ता {target_id} की पहुंच वापस ले ली गई।")
        try:
            bot.send_message(target_id, "🚫 आपकी पहुंच समाप्त कर दी गई है।")
        except Exception:
            pass
    except Exception:
        bot.reply_to(message, "⚠️ सही तरीका: /revoke &lt;आईडी&gt;")


@bot.message_handler(commands=['users'])
@owner_only
def list_users(message):
    users = list(users_db.find().sort("status", 1))
    if not users:
        return bot.reply_to(message, "कोई उपयोगकर्ता नहीं।")
    lines = [f"👥 <b>कुल उपयोगकर्ता: {len(users)}</b>\n"]
    for u in users[:50]:
        extra = f" — {days_left(u)} दिन बचे" if u.get("status") == "approved" else ""
        lines.append(f"• <code>{u['_id']}</code> {u.get('name', '')} [{u.get('status')}]{extra}")
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=['pending'])
@owner_only
def list_pending(message):
    users = list(users_db.find({"status": "pending"}))
    if not users:
        return bot.reply_to(message, "कोई लंबित अनुरोध नहीं।")
    lines = ["⏳ <b>लंबित अनुरोध</b>\n"]
    for u in users:
        lines.append(f"• {u.get('name', '')} <code>{u['_id']}</code>\n  /approve {u['_id']} 30 | /reject {u['_id']}")
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=['stats'])
@owner_only
def stats(message):
    counts = {s: queue.count_documents({"status": s}) for s in STATUS_ICON}
    approved = users_db.count_documents({"status": "approved", "expires_at": {"$gt": time.time()}})
    pending = users_db.count_documents({"status": "pending"})
    day_ago = time.time() - 86400
    done_today = queue.count_documents({"status": "done", "finished_at": {"$gt": day_ago}})
    text = (
        "📊 <b>आंकड़े</b>\n\n"
        f"⏳ कतार में: {counts['queued']}\n"
        f"🔄 चल रहे: {counts['running']}\n"
        f"✅ पूरे: {counts['done']} (आज: {done_today})\n"
        f"❌ विफल: {counts['failed']}\n"
        f"🚫 रद्द: {counts['cancelled']}\n\n"
        f"👥 सक्रिय उपयोगकर्ता: {approved}\n"
        f"🙋 लंबित अनुरोध: {pending}"
    )
    bot.reply_to(message, text)


@bot.message_handler(commands=['clearfailed'])
@owner_only
def clear_failed(message):
    res = queue.delete_many({"status": {"$in": ["failed", "cancelled"]}})
    bot.reply_to(message, f"🧹 {res.deleted_count} विफल/रद्द काम हटाए गए।")


@bot.message_handler(commands=['broadcast'])
@owner_only
def broadcast(message):
    text = message.text.partition(" ")[2].strip()
    if not text:
        return bot.reply_to(message, "⚠️ सही तरीका: /broadcast &lt;संदेश&gt;")
    sent = 0
    for u in users_db.find({"status": "approved"}):
        try:
            bot.send_message(u["_id"], f"📢 <b>सूचना</b>\n\n{text}")
            sent += 1
        except Exception:
            pass
    bot.reply_to(message, f"📢 {sent} उपयोगकर्ताओं को भेजा गया।")


# ------------------------- documents -------------------------
@bot.message_handler(content_types=['document'])
def handle_docs(message):
    chat_id = message.chat.id
    if not is_approved(chat_id):
        return bot.reply_to(message, "🔒 आपके पास इस बॉट का उपयोग करने की अनुमति नहीं है। कृपया /request भेजें।")

    doc = message.document
    try:
        if not (doc.file_name or "").lower().endswith('.txt'):
            return bot.reply_to(message, "❌ कृपया केवल .txt फाइलें ही भेजें।")

        if doc.file_size > MAX_FILE_MB * 1024 * 1024:
            return bot.reply_to(message, f"❌ फाइल {MAX_FILE_MB} MB से बड़ी है।")

        if not is_owner(chat_id):
            active = queue.count_documents({"chat_id": chat_id, "status": {"$in": ["queued", "running"]}})
            if active >= MAX_ACTIVE_JOBS_PER_USER:
                return bot.reply_to(message, f"⚠️ आपके पहले से {active} काम कतार में हैं। कृपया उनके पूरे होने की प्रतीक्षा करें।")

        file_info = bot.get_file(doc.file_id)

        job = {
            "chat_id": chat_id,
            "file_path": file_info.file_path,
            "file_name": doc.file_name,
            "file_size": doc.file_size,
            "status": "queued",
            "progress": 0,
            "timestamp": time.time()
        }
        result = queue.insert_one(job)
        pos = queue_position(result.inserted_id)

        bot.reply_to(message,
                     f"✅ <b>{doc.file_name}</b> कतार में लग गई है!\n"
                     f"📍 कतार में स्थान: #{pos}\n"
                     f"वर्कर सर्वर इसे जल्द ही अनुवाद करके भेज देगा। स्थिति के लिए /status भेजें।")
        log.info(f"Queued job {result.inserted_id} from {chat_id}: {doc.file_name}")

    except Exception as e:
        log.error(f"handle_docs error: {e}")
        bot.reply_to(message, f"⚠️ त्रुटि: {e}")


@bot.message_handler(func=lambda m: True, content_types=['text'])
def fallback(message):
    if is_approved(message.chat.id):
        bot.reply_to(message, "📄 कृपया अनुवाद के लिए .txt फाइल भेजें। मदद के लिए /help।")
    else:
        bot.reply_to(message, "🔒 पहुंच के लिए /request भेजें।")


if __name__ == "__main__":
    log.info("मास्टर सर्वर चालू हो गया है... (संदेशों की प्रतीक्षा में)")
    bot.infinity_polling(timeout=30, long_polling_timeout=30)
