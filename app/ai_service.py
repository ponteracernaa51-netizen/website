import google.generativeai as genai
import json
from app.config import settings

# Настройка Gemini
genai.configure(api_key=settings.GEMINI_API_KEY)

# ✅ ИСПОЛЬЗУЕМ ВАШУ ДОСТУПНУЮ МОДЕЛЬ
model = genai.GenerativeModel('gemini-2.0-flash') 

async def evaluate_translation(original: str, user_translation: str, direction: str, interface_lang: str):
    
    lang_names = {"ru": "Russian", "en": "English", "uz": "Uzbek"}
    explanation_lang = lang_names.get(interface_lang, "English")

    prompt = f"""
    Act as an expert language tutor.
    Task: Evaluate the student's translation.
    
    Context:
    - Translation Direction: {direction}
    - Original Phrase: "{original}"
    - Student's Translation: "{user_translation}"
    
    Output Requirements:
    1. Give a score (0-100).
    2. Provide the ideal translation.
    3. Explain errors or give praise in {explanation_lang} language.
    4. Return ONLY raw JSON format without markdown formatting (no ```json):
    {{
        "score": int,
        "explanation": "string",
        "ideal_translation": "string"
    }}
    """

    try:
        response = await model.generate_content_async(prompt)
        
        # Очистка от возможного форматирования markdown
        clean_text = response.text.replace("```json", "").replace("```", "").strip()
        
        # Если вдруг пришел пустой ответ
        if not clean_text:
            raise ValueError("Empty response from AI")

        return json.loads(clean_text)
    except Exception as e:
        print(f"AI Error: {e}")
        return {
            "score": 0,
            "explanation": f"Ошибка соединения с AI. Попробуйте еще раз. ({str(e)})",
            "ideal_translation": "Error"
        }