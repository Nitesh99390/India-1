import os
import time
import requests
from pymongo import MongoClient
from deep_translator import GoogleTranslator

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
    # 4000 अक्षरों के टुकड़ों में बांटना (Google Translate की लिमिट से बचने के लिए)
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    translated = ""
    for chunk in chunks:
        if chunk.strip():
            translated += translator.translate(chunk) + "\n"
    return translated

print("Worker Server Started... Waiting for jobs.")

while True:
    # MongoDB से कतार में लगा पहला काम उठाएं और उसे 'running' कर दें (ताकि दूसरा VPS इसे न उठाए)
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
            print(f"Processing job for chat: {chat_id}")
            
            # 1. Download
            download_file(job["file_path"], original_file)
            
            # 2. Extract & Translate
            with open(original_file, 'r', encoding='utf-8') as f:
                text = f.read()
            hi_text = translate_text(text)
            
            # 3. Save
            with open(translated_file, 'w', encoding='utf-8') as f:
                f.write(hi_text)
                
            # 4. Upload to Telegram
            upload_file(chat_id, translated_file)
            
            # 5. Mark as done & Cleanup
            queue.update_one({"_id": job["_id"]}, {"$set": {"status": "done"}})
            os.remove(original_file)
            os.remove(translated_file)
            print("Job Completed!")
            
        except Exception as e:
            print(f"Error: {e}")
            queue.update_one({"_id": job["_id"]}, {"$set": {"status": "failed", "error": str(e)}})
    else:
        # अगर कोई काम नहीं है, तो 3 सेकंड इंतज़ार करें
        time.sleep(3)
