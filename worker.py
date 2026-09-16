import os
import time
import logging
import requests
from pymongo import MongoClient
from deep_translator import GoogleTranslator
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [WORKER] %(levelname)s: %(message)s")
log = logging.getLogger("worker")


# --- रेंडर को चकमा देने के लिए डमी सर्वर ---
def keep_alive():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Worker is Live!")

        def log_message(self, *args):
            pass

    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


keep_alive()
# -----------------------------------------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
TARGET_LANG = os.getenv("TARGET_LANG", "hi")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", 4500))          # Google limit 5000 chars
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))
STALE_JOB_SECONDS = int(os.getenv("STALE_JOB_SECONDS", 1800))  # 30 min
PROGRESS_EVERY = int(os.getenv("PROGRESS_EVERY", 10))    # chunks
WORK_DIR = os.getenv("WORK_DIR", "/tmp/novel_jobs")

os.makedirs(WORK_DIR, exist_ok=True)

db = MongoClient(MONGO_URI)["novel_db"]
queue = db["jobs"]
translator = GoogleTranslator(source='auto', target=TARGET_LANG)

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_FILE = f"https://api.telegram.org/file/bot{BOT_TOKEN}"


# ------------------------- Telegram helpers -------------------------
def send_message(chat_id, text, reply_to=None):
    try:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        r = requests.post(f"{TG_API}/sendMessage", data=payload, timeout=30)
        return r.json().get("result", {}).get("message_id")
    except Exception as e:
        log.warning(f"sendMessage failed: {e}")
        return None


def edit_message(chat_id, message_id, text):
    if not message_id:
        return
    try:
        requests.post(f"{TG_API}/editMessageText",
                      data={"chat_id": chat_id, "message_id": message_id,
                            "text": text, "parse_mode": "HTML"}, timeout=30)
    except Exception as e:
        log.debug(f"editMessageText failed: {e}")


def download_file(file_path, save_name):
    url = f"{TG_FILE}/{file_path}"
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        with open(save_name, 'wb') as f:
            for chunk in response.iter_content(chunk_size=1 << 16):
                f.write(chunk)


def upload_file(chat_id, file_name, caption=None):
    with open(file_name, 'rb') as f:
        data = {'chat_id': chat_id}
        if caption:
            data['caption'] = caption
        r = requests.post(f"{TG_API}/sendDocument", data=data, files={'document': f}, timeout=300)
        r.raise_for_status()
        return r.json()


# ------------------------- Text helpers -------------------------
def read_text_any_encoding(path):
    for enc in ("utf-8", "utf-8-sig", "utf-16", "gbk", "cp1252", "latin-1"):
        try:
            with open(path, 'r', encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()


def split_into_chunks(text, limit=CHUNK_SIZE):
    """Paragraph-aware chunking so sentences aren't cut mid-way."""
    chunks, current = [], ""
    for para in text.split("\n"):
        # A single paragraph bigger than limit -> hard split
        while len(para) > limit:
            if current:
                chunks.append(current)
                current = ""
            cut = para.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            chunks.append(para[:cut])
            para = para[cut:].lstrip()
        if len(current) + len(para) + 1 > limit:
            chunks.append(current)
            current = para
        else:
            current = para if not current else current + "\n" + para
    if current:
        chunks.append(current)
    return chunks


def translate_chunk(chunk):
    if not chunk.strip():
        return chunk
    delay = 2
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = translator.translate(chunk)
            return result if result else chunk
        except Exception as e:
            log.warning(f"translate attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(delay)
                delay *= 2
    return chunk  # fallback: keep original


def translate_text(text, on_progress=None):
    chunks = split_into_chunks(text)
    total = len(chunks)
    out = []
    for i, chunk in enumerate(chunks, 1):
        out.append(translate_chunk(chunk))
        if on_progress and (i % PROGRESS_EVERY == 0 or i == total):
            on_progress(i, total)
    return "\n".join(out)


# ------------------------- Job processing -------------------------
def requeue_stale_jobs():
    """If a worker died mid-job, put the job back in queue."""
    cutoff = time.time() - STALE_JOB_SECONDS
    res = queue.update_many(
        {"status": "running", "started_at": {"$lt": cutoff}},
        {"$set": {"status": "queued"}, "$inc": {"attempts": 1}}
    )
    if res.modified_count:
        log.info(f"Re-queued {res.modified_count} stale job(s)")


def process_job(job):
    chat_id = job["chat_id"]
    job_id = job["_id"]
    safe_name = os.path.basename(job["file_name"]).replace(" ", "_")
    original_file = os.path.join(WORK_DIR, f"original_{job_id}_{safe_name}")
    translated_file = os.path.join(WORK_DIR, f"{TARGET_LANG}_{safe_name}")
    started = time.time()

    status_msg = send_message(chat_id, f"⚙️ <b>{safe_name}</b> पर काम शुरू हो गया है...\n📥 फाइल डाउनलोड हो रही है...")

    try:
        log.info(f"Job {job_id} for chat {chat_id}: {safe_name}")
        download_file(job["file_path"], original_file)
        text = read_text_any_encoding(original_file)

        if not text.strip():
            raise ValueError("फाइल खाली है")

        total_chars = len(text)
        queue.update_one({"_id": job_id}, {"$set": {"total_chars": total_chars}})

        def on_progress(done, total):
            pct = int(done * 100 / total)
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            edit_message(chat_id, status_msg,
                         f"🔄 <b>{safe_name}</b> का अनुवाद हो रहा है...\n[{bar}] {pct}%  ({done}/{total} भाग)")
            queue.update_one({"_id": job_id}, {"$set": {"progress": pct}})

        hi_text = translate_text(text, on_progress)

        with open(translated_file, 'w', encoding='utf-8') as f:
            f.write(hi_text)

        elapsed = int(time.time() - started)
        upload_file(chat_id, translated_file,
                    caption=f"✅ अनुवाद पूरा!\n📄 {safe_name}\n🔤 {total_chars:,} अक्षर\n⏱ {elapsed // 60}m {elapsed % 60}s")
        edit_message(chat_id, status_msg, f"✅ <b>{safe_name}</b> का अनुवाद पूरा हो गया!")

        queue.update_one({"_id": job_id}, {"$set": {
            "status": "done", "progress": 100,
            "finished_at": time.time(), "elapsed": elapsed}})
        log.info(f"Job {job_id} done in {elapsed}s")

    except Exception as e:
        log.error(f"Job {job_id} failed: {e}")
        queue.update_one({"_id": job_id}, {"$set": {
            "status": "failed", "error": str(e), "finished_at": time.time()}})
        edit_message(chat_id, status_msg,
                     f"❌ <b>{safe_name}</b> का अनुवाद विफल रहा।\nकारण: <code>{str(e)[:300]}</code>\n\nकृपया फाइल दोबारा भेजें।")
    finally:
        for p in (original_file, translated_file):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass


def main():
    log.info(f"वर्कर सर्वर चालू हो गया है... (कतार में काम की प्रतीक्षा में) target={TARGET_LANG}")
    last_stale_check = 0
    while True:
        try:
            if time.time() - last_stale_check > 60:
                requeue_stale_jobs()
                last_stale_check = time.time()

            job = queue.find_one_and_update(
                {"status": "queued"},
                {"$set": {"status": "running", "started_at": time.time()}},
                sort=[("timestamp", 1)]
            )
            if job:
                process_job(job)
            else:
                time.sleep(3)
        except Exception as e:
            log.error(f"Main loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
