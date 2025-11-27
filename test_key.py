import asyncio
import os
from openai import AsyncOpenAI

# 1. ВСТАВЬТЕ ВАШ КЛЮЧ СЮДА ВНУТРЬ КАВЫЧЕК
# Не используйте os.getenv, впишите его прямо руками для проверки!
API_KEY = "C" 

async def test_connection():
    print(f"Testing Key: {API_KEY[:4]}...{API_KEY[-4:]}")
    
    client = AsyncOpenAI(
        api_key=API_KEY,
        base_url="https://api.groq.com/openai/v1"
    )

    try:
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "user", "content": "Say 'Hello' if this works."}
            ]
        )
        print("\n✅ УСПЕХ! Ключ работает.")
        print("Ответ сервера:", response.choices[0].message.content)
    except Exception as e:
        print("\n❌ ОШИБКА! Ключ не работает.")
        print(f"Детали ошибки: {e}")

if __name__ == "__main__":
    asyncio.run(test_connection())