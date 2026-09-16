import os
import time
import requests
from pymongo import MongoClient
from deep_translator import GoogleTranslator
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- रेंडर को चकमा देने के लिए डमी सर्वर ---
def keep_alive():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Worker is Live!")
    
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

keep_alive()
# -----------------------------------------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

db = MongoClient(MONGO_URI)["novel_db"]
queue = db["jobs"]
translator = GoogleTranslator(source='auto', target='hi')

def download_file(file_path, save_name):
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    response = requests.get(url)
    with open(save_name, 'wb') as f:
        f.write(response.content)

def upload_file(chat_id, file_name):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    with open(file_name, 'rb') as f:
        requests.post(url, data={'chat_id': chat_id}, files={'document': f})

def translate_text(text):
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    translated = ""
    for chunk in chunks:
        if chunk.strip():
            try:
                translated += translator.translate(chunk) + "\n"
            except:
                translated += chunk + "\n"
    return translated

print("वर्कर सर्वर चालू हो गया है... (कतार में काम की प्रतीक्षा में)")

while True:
    job = queue.find_one_and_update(
        {"status": "queued"}, 
        {"$set": {"status": "running"}},
        sort=[("timestamp", 1)]
    )
    
    if job:
        chat_id = job["chat_id"]
        original_file = "original_" + job["file_name"]
        translated_file = "hi_" + job["file_name"]
        
        try:
            print(f"चैट के लिए काम शुरू किया गया: {chat_id}")
            
            download_file(job["file_path"], original_file)
            
            with open(original_file, 'r', encoding='utf-8') as f:
                text = f.read()
            hi_text = translate_text(text)
            
            with open(translated_file, 'w', encoding='utf-8') as f:
                f.write(hi_text)
                
            upload_file(chat_id, translated_file)
            
            queue.update_one({"_id": job["_id"]}, {"$set": {"status": "done"}})
            if os.path.exists(original_file): os.remove(original_file)
            if os.path.exists(translated_file): os.remove(translated_file)
            print("काम सफलतापूर्वक पूरा हो गया!")
            
        except Exception as e:
            print(f"काम करने में त्रुटि आई: {e}")
            queue.update_one({"_id": job["_id"]}, {"$set": {"status": "failed", "error": str(e)}})
            if os.path.exists(original_file): os.remove(original_file)
            if os.path.exists(translated_file): os.remove(translated_file)
    else:
        time.sleep(3)
