import asyncio
import logging
import json
from typing import Optional, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from app.config import settings

logger = logging.getLogger(__name__)

# --- CONFIG ---
class ErrorType(str, Enum):
    NONE = "None"
    CAPITALIZATION = "Capitalization"
    GRAMMAR = "Grammar"
    VOCABULARY = "Vocabulary"
    SPELLING = "Spelling"
    CRITICAL = "Critical" # Смысл неверен

class EvaluationResponse(BaseModel):
    score: int = Field(..., ge=0, le=100)
    deductions: str
    explanation: str
    ideal_translation: str  # Здесь мы вернем Reference или исправленный вариант
    error_type: ErrorType

_client: Optional[AsyncOpenAI] = None

def _get_client():
    global _client
    if _client: return _client
    # Убедитесь, что ключи в .env верные для Gemini или Llama (Groq)
    if settings.LLAMA_API_KEY:
        _client = AsyncOpenAI(api_key=settings.LLAMA_API_KEY, base_url=settings.LLAMA_BASE_URL)
    return _client

def clean_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```json"): text = text[7:]
    if text.startswith("```"): text = text[3:]
    if text.endswith("```"): text = text[:-3]
    return text.strip()

# --- MAIN LOGIC ---
async def evaluate_translation(
    original: str, 
    reference_translation: str,  # <--- НОВЫЙ АРГУМЕНТ (Текст из БД: text_en или text_uz)
    user_translation: str, 
    direction: str, 
    interface_lang: str
) -> Dict[str, Any]:
    
    # Базовая валидация
    if not original.strip() or not user_translation.strip():
        return {"score": 0, "explanation": "Empty input", "error_type": "Critical"}

    client = _get_client()
    if not client: 
        return {"score": 0, "explanation": "AI Config Error", "error_type": "Critical"}

    # Определение языков
    lang_map = {"en": "English", "uz": "Uzbek", "ru": "Russian"}
    try:
        parts = direction.lower().split('-') # пример: ru-en
        src_lang = lang_map.get(parts[0], "Russian")
        tgt_lang = lang_map.get(parts[1], "English")
    except: 
        src_lang, tgt_lang = "Russian", "English"

    # Настройка языка объяснения (Feedback Language)
# Настройка языка объяснения (Feedback Language)
    explain_instr = f"in {lang_map.get(interface_lang, 'English')}"
    if "uz" in interface_lang.lower():
        # Было: "Xato: ..."
        # Стало: "Izoh: ..." (чтобы не пугать слово "Ошибка" при синонимах)
        explain_instr = """
        in UZBEK (Latin script).
        Format: 
        - If score is 100: "Barakalla! Tarjima aniq."
        - If score is 90-99 (Synonym): "To'g'ri: [Reference]. Izoh: Sizning varianingiz ham to'g'ri (sinonim)."
        - If score < 90: "To'g'ri: [Reference]. Xato: [Explain error]."
        """

    # Новый System Prompt для СРАВНЕНИЯ
    system_prompt = f"""
    You are a strict Language Examiner. 
    Source Language: {src_lang}
    Target Language: {tgt_lang}
    
    SPECIAL RULE FOR UZBEK SOURCE:
    - The Uzbek word "U" implies both "He" and "She". 
    - If Source is Uzbek and Reference uses "He", but Student uses "She" (or vice versa), ACCEPT IT as correct. Do not deduct points for gender mismatch unless context clearly defines it.
    
    TASK:
    Compare the Student's translation against the OFFICIAL REFERENCE.

    SCORING RULES:
    1. EXACT MATCH: If Student == Reference (ignoring case/punctuation) -> Score 100.
    2. SYNONYMS: If Student uses valid synonyms (e.g., 'car' vs 'automobile') AND grammar is perfect -> Score 95-100. Mention that the Reference uses a different word but Student is correct.
    3. GRAMMAR ERROR: If meaning is close but grammar is wrong -> Score 60-80.
    4. WRONG MEANING: If Student says something totally different from Reference -> Score 0-40.

    OUTPUT INSTRUCTIONS:
    - Provide feedback {explain_instr}.
    - 'ideal_translation' field must contain the OFFICIAL REFERENCE provided below.
    - 'deductions' field: Short summary of errors.

    OUTPUT JSON FORMAT:
    {{
        "score": integer (0-100),
        "deductions": "string",
        "explanation": "string",
        "ideal_translation": "string",
        "error_type": "None | Grammar | Vocabulary | Spelling | Critical"
    }}
    """

    # Данные для ИИ
    user_prompt = f"""
    Original Phrase ({src_lang}): "{original}"
    OFFICIAL REFERENCE ({tgt_lang}): "{reference_translation}"
    Student Translation: "{user_translation}"
    """
    
    try:
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile", # Или gemini-2.0-flash
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1, # Ставим низкую температуру для строгости
            response_format={"type": "json_object"} # Force JSON
        )
        
        content = response.choices[0].message.content
        result = json.loads(clean_json(content))
        
        # Страховка: если ИИ решил поменять эталон, принудительно возвращаем эталон из БД
        # Но если синоним верный (Score=100), можно оставить как есть или показать оба варианта.
        # Для простоты вернем Reference из базы, чтобы юзер знал, чего мы от него хотели.
        if result.get("score") < 100:
             result["ideal_translation"] = reference_translation

        return result

    except Exception as e:
        logger.error(f"AI Evaluation Error: {e}")
        # Фолбэк на случай ошибки ИИ
        return {
            "score": 0, 
            "explanation": "System error during check.", 
            "ideal_translation": reference_translation,
            "error_type": "Critical"
        }
