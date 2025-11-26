import google.generativeai as genai
import json
from app.config import settings

genai.configure(api_key=settings.GEMINI_API_KEY)

# Схема ответа (без изменений, она правильная)
response_schema = {
    "type": "OBJECT",
    "properties": {
        "score": {"type": "INTEGER"},
        "deductions": {"type": "STRING"},
        "explanation": {"type": "STRING"},
        "ideal_translation": {"type": "STRING"},
        "error_type": {"type": "STRING", "enum": ["None", "Grammar", "Vocabulary", "Spelling", "Style", "Critical"]}
    },
    "required": ["score", "deductions", "explanation", "ideal_translation", "error_type"]
}

generation_config = {
    "temperature": 0.0, # Ноль фантазий, только логика
    "top_p": 0.95,
    "max_output_tokens": 1024,
    "response_mime_type": "application/json",
    "response_schema": response_schema
}

model = genai.GenerativeModel(
    model_name='gemini-2.0-flash-lite',
    generation_config=generation_config
)

def parse_languages(direction: str):
    lang_map = {
        "en": "English", "ru": "Russian", "uz": "Uzbek",
        "de": "German", "fr": "French", "es": "Spanish"
    }
    try:
        source, target = direction.lower().split('-')
        return lang_map.get(source, source), lang_map.get(target, target)
    except ValueError:
        return "Unknown", "Unknown"

async def evaluate_translation(original: str, user_translation: str, direction: str, interface_lang: str):
    
    # 1. Определяем языки
    lang_names = {"ru": "Russian", "en": "English", "uz": "Uzbek"}
    
    # Язык, на котором пользователь читает интерфейс (для объяснений ошибок)
    explanation_lang = lang_names.get(interface_lang, "English")
    
    # Направление перевода
    source_lang, target_lang = parse_languages(direction)

    user_clean = user_translation.strip()
    original_clean = original.strip()

    # 2. ПРОМПТ С ЖЕСТКИМИ ЯЗЫКОВЫМИ ПРАВИЛАМИ
    prompt = f"""
    Role: Strict Linguistic Algo.
    
    TASK: Evaluate translation quality.
    
    PARAMETERS:
    - Source Language: {source_lang} (Original Text)
    - Target Language: {target_lang} (The language the student is trying to write in)
    - Explanation Language: {explanation_lang} (The language for feedback)

    INPUT:
    - Original: "{original_clean}"
    - Student Input: "{user_clean}"

    ⚠️ STRICT LANGUAGE RULES FOR OUTPUT FIELDS:
    1. "ideal_translation": MUST be in **{target_lang}**. (Do NOT write it in {source_lang} or {explanation_lang}).
    2. "explanation": MUST be in **{explanation_lang}**.
    3. "deductions": Can be short technical notes (e.g., "-2 typo").

    EVALUATION LOGIC (Start 100):
    - If Student Input has the correct meaning and grammar -> Score 100.
    - If Student Input is a valid synonym -> Score 100 (Do not correct it to your preferred word).
    - If Capitalization error (e.g. "paris" vs "Paris") -> -1 point.
    - If Spelling typo -> -2 points.
    - If Grammar error -> -5 to -10 points.
    - If Wrong meaning -> -50 points.

    ANTI-HALLUCINATION:
    - Never say "'Word' should be 'Word'" if they are the same.
    - If the only difference is case, say "Capitalization error".

    JSON OUTPUT FORMAT:
    {{
        "score": integer,
        "deductions": "string",
        "explanation": "string (in {explanation_lang})",
        "ideal_translation": "string (in {target_lang})",
        "error_type": "string"
    }}
    """

    try:
        response = await model.generate_content_async(prompt)
        return json.loads(response.text)

    except Exception as e:
        print(f"AI Error: {e}")
        return {
            "score": 0,
            "deductions": "System Error",
            "explanation": "Xatolik yuz berdi. (Error)",
            "ideal_translation": "Error",
            "error_type": "Critical"
        }