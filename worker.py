import os
import time
import requests
from pymongo import MongoClient
from deep_translator import GoogleTranslator

# Environment Variables से चाबियां उठाना
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
    # 4000 अक्षरों के टुकड़ों में बांटना
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    translated = ""
    for chunk in chunks:
        if chunk.strip():
            try:
                translated += translator.translate(chunk) + "\n"
            except:
                translated += chunk + "\n"
    return translated

print("Worker Server Started... (Waiting for jobs in Queue)")

while True:
    # कतार से काम उठाना और उसे 'running' कर देना
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
                
            # 4. Upload
            upload_file(chat_id, translated_file)
            
            # 5. Cleanup
            queue.update_one({"_id": job["_id"]}, {"$set": {"status": "done"}})
            if os.path.exists(original_file): os.remove(original_file)
            if os.path.exists(translated_file): os.remove(translated_file)
            print("Job Completed successfully!")
            
        except Exception as e:
            print(f"Error processing job: {e}")
            queue.update_one({"_id": job["_id"]}, {"$set": {"status": "failed", "error": str(e)}})
            if os.path.exists(original_file): os.remove(original_file)
            if os.path.exists(translated_file): os.remove(translated_file)
    else:
        time.sleep(3)
