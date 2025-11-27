import google.generativeai as genai
import json
import re
from app.config import settings

genai.configure(api_key=settings.GEMINI_API_KEY)

# ==========================================
# 1. СХЕМА ОТВЕТА
# ==========================================
response_schema = {
    "type": "OBJECT",
    "properties": {
        "score": {"type": "INTEGER"},
        "deductions": {"type": "STRING"},
        "explanation": {"type": "STRING"},
        "ideal_translation": {"type": "STRING"},
        "error_type": {"type": "STRING", "enum": ["None", "Capitalization", "Style", "Spelling", "Grammar", "Vocabulary", "Critical"]}
    },
    "required": ["score", "deductions", "explanation", "ideal_translation", "error_type"]
}

generation_config = {
    "temperature": 0.0,
    "top_p": 0.95,
    "max_output_tokens": 2048,
    "response_mime_type": "application/json",
    "response_schema": response_schema
}

# ==========================================
# 2. МОЗГ СИСТЕМЫ (SYSTEM INSTRUCTION)
# ==========================================
SYSTEM_INSTRUCTION = """
Role: Surgical Language Corrector.

ALGORITHM FOR ERROR ANALYSIS:

1. **EXISTENCE CHECK (Anti-Hallucination):**
   - BEFORE saying "Add article 'a'", LOOK at the student's text.
   - Student: "weiting a bus".
   - *Check:* Does "a" exist? YES.
   - *Action:* DO NOT correct the article. Only correct the missing preposition or spelling.

2. **PREPOSITION LOGIC ("Wait for"):**
   - "Wait a bus" -> WRONG.
   - "Wait FOR a bus" -> RIGHT.
   - Error: "Grammar (Missing Preposition)". Penalty: -10.

3. **SPELLING vs STYLE:**
   - "weiting" -> Spelling error (-5).
   - "he" (lowercase start) -> Capitalization (-2).

SCORING "he is weiting a bus":
- "he" -> -2.
- "weiting" -> -5.
- "waiting [missing for] a bus" -> -10.
- "a bus" -> Correct (No deduction).
- Final Score: ~83.

OUTPUT RULES:
- `explanation`: Write ONLY in {explanation_lang}. Be precise: "Add 'for' after 'waiting'. Fix spelling 'weiting'." (Do not mention the article).
- `ideal_translation`: "He is waiting for a bus."
"""

model = genai.GenerativeModel(
    model_name='gemini-2.0-flash-lite',
    generation_config=generation_config,
    system_instruction=SYSTEM_INSTRUCTION
)

# ==========================================
# 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def clean_json_string(text: str):
    """Очистка от Markdown"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text

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

# ==========================================
# 4. ВЫПОЛНЕНИЕ
# ==========================================
async def evaluate_translation(original: str, user_translation: str, direction: str, interface_lang: str):
    
    lang_names = {"ru": "Russian", "en": "English", "uz": "Uzbek"}
    explanation_lang = lang_names.get(interface_lang, "English")
    source_lang, target_lang = parse_languages(direction)

    prompt = f"""
    [CONFIG]
    Source: {source_lang}
    Target: {target_lang}
    Explain in: {explanation_lang}

    [DATA]
    Original: "{original.strip()}"
    Student: "{user_translation}" 
    """

    try:
        response = await model.generate_content_async(prompt)
        cleaned_response = clean_json_string(response.text)
        return json.loads(cleaned_response)

    except Exception as e:
        print(f"AI Error: {e}")
        return {
            "score": 0,
            "deductions": "System Error",
            "explanation": "Xatolik yuz berdi.",
            "ideal_translation": "Error",
            "error_type": "Critical"
        }