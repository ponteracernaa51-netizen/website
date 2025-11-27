import asyncio
import logging
import json
from typing import Optional, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field, validator  # Для валидации ответа (опционально)
from openai import AsyncOpenAI
from app.config import settings

logger = logging.getLogger(__name__)

# Enum для error_type (для consistency)
class ErrorType(str, Enum):
    NONE = "None"
    CAPITALIZATION = "Capitalization"
    STYLE = "Style"
    SPELLING = "Spelling"
    GRAMMAR = "Grammar"
    VOCABULARY = "Vocabulary"
    CRITICAL = "Critical"

# Pydantic модель для ответа (валидация)
class EvaluationResponse(BaseModel):
    score: int = Field(..., ge=0, le=100)
    deductions: str
    explanation: str
    ideal_translation: str
    error_type: ErrorType

    @validator('error_type')
    def validate_error_type(cls, v):
        if v not in ErrorType:
            raise ValueError(f'Invalid error_type: {v}')
        return v

# Глобальный клиент с lock для thread-safety
_client: Optional[AsyncOpenAI] = None
_client_lock = asyncio.Lock()

async def _init_client() -> Optional[AsyncOpenAI]:
    global _client
    async with _client_lock:
        if _client is not None:
            return _client

        if not settings.LLAMA_API_KEY:
            logger.error("LLAMA_API_KEY не найден.")
            return None

        try:
            _client = AsyncOpenAI(
                api_key=settings.LLAMA_API_KEY,
                base_url=settings.LLAMA_BASE_URL,  # e.g., "https://api.groq.com/openai/v1"
                timeout=30.0  # Таймаут 30 сек
            )
            logger.info("LLaMA Client initialized.")
            return _client
        except Exception as e:
            logger.error(f"LLaMA Init Error: {e}")
            return None

async def evaluate_translation(
    original: str,
    user_translation: str,
    direction: str,
    interface_lang: str
) -> Dict[str, Any]:
    """
    Оценивает перевод с использованием LLaMA 3.3 на Groq.
    
    Args:
        original: Оригинальный текст.
        user_translation: Перевод пользователя.
        direction: Направление, e.g., "en-ru".
        interface_lang: Язык интерфейса для explanation, e.g., "ru".
    
    Returns:
        Dict с score, explanation и т.д.
    """
    # Валидация входов
    if not original.strip() or not user_translation.strip():
        logger.warning("Пустой original или user_translation.")
        return {
            "score": 0,
            "deductions": "Empty input",
            "explanation": "Входные данные пусты.",
            "ideal_translation": "",
            "error_type": ErrorType.CRITICAL.value
        }
    
    if len(direction.strip()) < 3 or '-' not in direction:
        logger.error(f"Invalid direction: {direction}")
        return {
            "score": 0,
            "deductions": "Invalid direction",
            "explanation": "Неверный формат направления перевода (ожидается 'en-ru').",
            "ideal_translation": "",
            "error_type": ErrorType.CRITICAL.value
        }

    client = await _init_client()
    if not client:
        return {
            "score": 0,
            "deductions": "Service config error",
            "explanation": "Ошибка конфигурации сервиса.",
            "ideal_translation": "",
            "error_type": ErrorType.CRITICAL.value
        }

    # Расширенный lang_map
    lang_map = {
        "en": "English", "ru": "Russian", "uz": "Uzbek",
        "de": "German", "fr": "French", "es": "Spanish",
        "it": "Italian", "pt": "Portuguese", "ja": "Japanese", "zh": "Chinese"
    }
    
    try:
        src_code, tgt_code = direction.lower().strip().split('-')
        source_lang = lang_map.get(src_code, "Unknown Source")
        target_lang = lang_map.get(tgt_code, "Unknown Target")
    except ValueError as e:
        logger.error(f"Direction parse error: {e}, direction={direction}")
        source_lang, target_lang = "Unknown Source", "Unknown Target"

    explanation_lang = lang_map.get(interface_lang.lower(), "English")

    # Системный промпт (строже для JSON)
    system_instruction = f"""
    You are a Strict Language Teacher. Respond ONLY with VALID JSON. No extra text.

    JSON SCHEMA (exact keys, no extras):
    {{
        "score": integer (0-100, strict penalties for errors),
        "deductions": string (brief summary of all errors, e.g., "Grammar: double verb; Spelling: 'he'->'the'"),
        "explanation": string (detailed fixes in {explanation_lang}, e.g., "Удалите 'are' (дубликат глагола). 'In' -> 'at' для точности. 'He' -> 'the'."),
        "ideal_translation": string (perfect translation in {target_lang}, natural and concise),
        "error_type": string (one of: "None", "Capitalization", "Style", "Spelling", "Grammar", "Vocabulary", "Critical")
    }}
    """

    # Пользовательский промпт (с примерами)
    user_prompt = f"""
    Analyze translation from {source_lang} ({src_code}) to {target_lang} ({tgt_code}).

    Original: "{original.strip()}"
    Student: "{user_translation.strip()}"

    RULES:
    1. Anti-Hallucination: Only critique existing words; don't invent missing ones.
    2. Grammar: Penalize harshly (e.g., "we are stayed" -> Critical, score <50).
    3. Typos: "he hotel" -> Spelling error, deduct 10-20 points.
    4. Score: 100 for perfect; deduct per error (e.g., 1 minor= -5, critical= -50).
    5. Ideal: Keep original meaning, natural phrasing.

    Example Input: Original: "We stayed at the hotel." Student: "We are stayed in he hotel."
    Example Output: {{"score":40,"deductions":"Grammar: double verb; Spelling: 'he'->'the'","explanation":"Удалите 'are' (дубликат глагола). 'In' -> 'at' для точности. 'He' -> 'the'.","ideal_translation":"We stayed at the hotel.","error_type":"Grammar"}}
    """

    # Основная модель и fallback
    models_to_try = ["llama-3.3-70b-versatile", "llama3-70b-8192"]  # Fallback на стабильную модель
    max_retries = 3

    for model in models_to_try:
        logger.info(f"Trying model: {model}")
        for attempt in range(max_retries):
            try:
                response = await client.chat.completions.create(
                    model=model,  # Исправлено: LLaMA 3.3 для Groq (или fallback)
                    messages=[
                        {"role": "system", "content": system_instruction},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.0,
                    max_tokens=512,
                    response_format={"type": "json_object"}
                )

                content = response.choices[0].message.content
                if not content:
                    raise ValueError("Empty response content")

                # Логируем usage
                usage = getattr(response, 'usage', None)
                if usage:
                    logger.info(f"Tokens used: prompt={usage.prompt_tokens}, completion={usage.completion_tokens}")

                # Парсинг и валидация
                result_dict = json.loads(content)
                result = EvaluationResponse(**result_dict).dict()  # Валидация через Pydantic
                logger.info(f"Evaluation success with {model}: score={result['score']}")
                return result

            except json.JSONDecodeError as e:
                logger.error(f"Attempt {attempt+1} with {model}: Invalid JSON: {content[:200] if 'content' in locals() else 'N/A'}... Error: {e}")
                if attempt == max_retries - 1:
                    break  # Переходим к следующей модели
                await asyncio.sleep(2 ** attempt)

            except openai.BadRequestError as e:
                error_msg = str(e)
                if "model_decommissioned" in error_msg:
                    logger.warning(f"Model {model} decommissioned. Switching to fallback.")
                    break  # Переходим к следующей модели без retry
                logger.error(f"Attempt {attempt+1} with {model}: BadRequest: {e}")
                if attempt == max_retries - 1:
                    break
                await asyncio.sleep(2 ** attempt)

            except Exception as e:
                logger.error(f"Attempt {attempt+1} with {model}: LLaMA API Error: {e}", exc_info=True)
                if attempt == max_retries - 1:
                    break
                await asyncio.sleep(2 ** attempt)

        # Если все retry для этой модели провалились, пробуем следующую

    # Fallback после всех моделей
    logger.error("All models failed.")
    return {
        "score": 0,
        "deductions": "Model Error",
        "explanation": "Ошибка модели. Обратитесь к администратору.",
        "ideal_translation": "",
        "error_type": ErrorType.CRITICAL.value
    }