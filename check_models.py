import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv() # Загружаем ключ из .env

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("❌ Ключ не найден в .env")
else:
    print(f"✅ Ключ найден: {api_key[:5]}...")
    genai.configure(api_key=api_key)
    
    print("\n🔍 Доступные модели:")
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                print(f" - {m.name}")
    except Exception as e:
        print(f"❌ Ошибка при запросе: {e}")