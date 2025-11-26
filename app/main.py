import uuid
from fastapi import FastAPI, Request, Form, Response, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse # <-- Вот здесь было изменение
from fastapi.templating import Jinja2Templates
from app.database import supabase
from app.ai_service import evaluate_translation
from app.translations import UI_TEXTS, TARGET_LANG_NAMES
import re

app = FastAPI(title="FluentEdgeAI")
templates = Jinja2Templates(directory="templates")

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_user_context(request: Request):
    """Собираем все настройки пользователя из кук"""
    user_id = request.cookies.get("fluent_user_id")
    if not user_id:
        user_id = str(uuid.uuid4())
        
    # Настройки по умолчанию
    lang = request.cookies.get("fluent_lang", "ru") # Язык интерфейса
    direction = request.cookies.get("fluent_dir", "RU-EN") # Направление
    is_auth = request.cookies.get("fluent_is_auth") == "true"
    
    return {
        "user_id": user_id,
        "lang": lang,
        "dir": direction,
        "is_auth": is_auth,
        "ui": UI_TEXTS.get(lang, UI_TEXTS["ru"]) # Тексты интерфейса
    }

def get_error_phrases(user_id):
    """
    Возвращает список ID фраз, где ПОСЛЕДНЯЯ попытка была < 90 баллов.
    Если пользователь исправил ошибку (сдал на 95), фраза сюда не попадет.
    """
    # 1. Берем ВСЕ попытки пользователя, от новых к старым
    attempts = supabase.table("user_attempts")\
        .select("phrase_id, ai_score")\
        .eq("user_id", user_id)\
        .order("created_at", desc=True)\
        .execute().data
    
    bad_phrase_ids = []
    seen_phrases = set()

    for a in attempts:
        pid = a['phrase_id']
        # Если мы эту фразу еще не проверяли (это самая свежая попытка)
        if pid not in seen_phrases:
            seen_phrases.add(pid)
            # Если оценка ниже 90 - это ошибка
            if a['ai_score'] < 90:
                bad_phrase_ids.append(pid)
    
    return bad_phrase_ids

# --- НАСТРОЙКИ И СБРОС ---

@app.get("/set_settings")
async def set_settings(request: Request, lang: str = None, direction: str = None):
    """Меняет язык или направление и перезагружает страницу"""
    redirect_url = request.headers.get("referer") or "/"
    response = RedirectResponse(url=redirect_url)
    
    if lang in ["ru", "en", "uz"]:
        response.set_cookie("fluent_lang", lang)
    
    if direction in ["RU-EN", "EN-RU", "UZ-EN", "EN-UZ"]:
        response.set_cookie("fluent_dir", direction)
        
    return response

@app.get("/reset_progress")
async def reset_progress(request: Request):
    """Удаляет историю ответов пользователя"""
    ctx = get_user_context(request)
    try:
        # Удаляем записи из БД
        supabase.table("user_attempts").delete().eq("user_id", ctx["user_id"]).execute()
    except Exception as e:
        print(f"Reset error: {e}")
        
    return RedirectResponse(url="/", status_code=302)

# --- АВТОРИЗАЦИЯ ---

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    ctx = get_user_context(request)
    return templates.TemplateResponse("login.html", {"request": request, "ctx": ctx})

@app.post("/auth_action")
async def auth_action(request: Request, email: str = Form(...), password: str = Form(...)):
    """ФИНАЛЬНАЯ ВЕРСИЯ: Исправлена ошибка UnboundLocalError"""
    
    # 1. Очистка и Валидация
    import re
    email = re.sub(r'[^a-zA-Z0-9@._-]', '', email).strip().lower()
    password = password.strip()
    
    if not email or "@" not in email:
        return HTMLResponse("<h3>Ошибка: Некорректный Email!</h3><a href='/login'>Назад</a>")
    if len(password) < 6:
        return HTMLResponse("<h3>Ошибка: Пароль должен быть не менее 6 символов!</h3><a href='/login'>Назад</a>")

    anon_id = request.cookies.get("fluent_user_id")
    
    # --- ИНИЦИАЛИЗАЦИЯ ПЕРЕМЕННЫХ (ВАЖНО!) ---
    user = None
    err_str = ""  # <--- Создаем переменную заранее, чтобы не было ошибки
    # -----------------------------------------

    print(f"🚀 Попытка входа/регистрации: {email}")

    # 3. Попытка ВХОДА (Login)
    try:
        res = supabase.auth.sign_in_with_password({"email": email, "password": password})
        if res.user:
            user = res.user
            print("✅ Успешный вход!")
    except Exception as login_error:
        err_str = str(login_error) # Записываем ошибку, если она есть
        print(f"ℹ️ Ошибка входа: {err_str}")

    # Проверка на неподтвержденную почту (теперь err_str точно существует)
    if "Email not confirmed" in err_str:
        return HTMLResponse(f"""
            <div style="font-family:sans-serif; max-width:400px; margin:50px auto; padding:20px; border:1px solid #ccc; border-radius:10px;">
                <h2 style="color:#e11d48;">Требуется подтверждение!</h2>
                <p>Supabase требует подтвердить Email.</p>
                <p>Зайдите в настройки Supabase -> Auth -> Providers -> Email и отключите "Confirm Email".</p>
                <a href='/login' style="display:block; margin-top:20px; color:#2563eb;">Вернуться назад</a>
            </div>
        """)

    # 4. Попытка РЕГИСТРАЦИИ (Sign Up), если вход не удался
    if not user:
        try:
            res = supabase.auth.sign_up({
                "email": email, 
                "password": password,
                "options": {"data": {"full_name": "User"}} 
            })
            
            if res.user and res.user.identities and len(res.user.identities) > 0:
                user = res.user
                print("✅ Успешная регистрация!")
            elif res.user and (not res.user.identities or len(res.user.identities) == 0):
                return HTMLResponse(f"<h3>Пользователь уже существует, но пароль неверный.</h3><a href='/login'>Назад</a>")
            else:
                return HTMLResponse(f"<h3>Регистрация отправлена. Проверьте настройки Supabase (Confirm Email).</h3><a href='/login'>Назад</a>")

        except Exception as reg_error:
            print(f"❌ Ошибка регистрации: {reg_error}")
            return HTMLResponse(f"<h3>Ошибка Supabase:</h3><p>{reg_error}</p><a href='/login'>Назад</a>")

    if not user:
         return HTMLResponse("<h3>Неизвестная ошибка: Пользователь не получен.</h3><a href='/login'>Назад</a>")

    # 5. Профиль и Статистика
    try:
        supabase.table("profiles").upsert({"id": user.id, "email": email}).execute()
        if anon_id and anon_id != user.id:
            supabase.table("user_attempts").update({"user_id": user.id}).eq("user_id", anon_id).execute()
    except Exception as e:
        print(f"⚠️ Ошибка БД (не критично): {e}")

    # 6. Успех
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie("fluent_user_id", user.id)
    response.set_cookie("fluent_is_auth", "true")
    
    return response

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/")
    response.delete_cookie("fluent_is_auth")
    # Генерируем новый анонимный ID
    response.set_cookie("fluent_user_id", str(uuid.uuid4()))
    return response

# --- ОСНОВНЫЕ СТРАНИЦЫ ---

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    ctx = get_user_context(request)
    
    # 1. Загружаем Уровни (сортируем по порядку)
    levels = supabase.table("levels").select("*").order("order_index").execute().data
    
    # 2. Загружаем Темы
    topics = supabase.table("topics").select("*").execute().data

    # 3. Группировка: Вкладываем темы внутрь уровней
    # Структура будет: levels = [ {..., "topics": [t1, t2]}, ... ]
    levels_with_topics = []
    for lvl in levels:
        # Находим темы, которые принадлежат этому уровню
        lvl_topics = [t for t in topics if t.get('level_id') == lvl['id']]
        
        # Добавляем уровень в список, только если в нем есть темы (чтобы не показывать пустые)
        if lvl_topics:
            lvl['topics'] = lvl_topics
            levels_with_topics.append(lvl)

    # 4. Статистика (без изменений)
    attempts = supabase.table("user_attempts").select("ai_score").eq("user_id", ctx["user_id"]).execute().data
    total = len(attempts)
    avg = sum(a['ai_score'] for a in attempts) // total if total > 0 else 0
    mistakes_count = len(get_error_phrases(ctx["user_id"]))

    response = templates.TemplateResponse("base.html", {
        "request": request, 
        "levels": levels_with_topics, # <--- ПЕРЕДАЕМ СГРУППИРОВАННЫЕ ДАННЫЕ
        "stats": {"total": total, "avg": avg},
        "mistakes_count": mistakes_count,
        "ctx": ctx
    })
    
    if not request.cookies.get("fluent_user_id"):
        response.set_cookie(key="fluent_user_id", value=ctx["user_id"])
        
    return response

@app.get("/training/{topic_slug}", response_class=HTMLResponse)
async def start_training(request: Request, topic_slug: str):
    ctx = get_user_context(request)
    
    # Определяем направление (Source -> Target)
    # Например RU-EN: source='ru', target='en'
    source_lang, target_lang = ctx["dir"].split("-")
    
    # 1. Тема
    topic_res = supabase.table("topics").select("id").eq("slug", topic_slug).execute()
    if not topic_res.data:
        return "Topic not found"
    topic_id = topic_res.data[0]['id']

    # 2. Пройденные фразы
    completed_ids = [
        x['phrase_id'] for x in 
        supabase.table("user_attempts").select("phrase_id").eq("user_id", ctx["user_id"]).gt("ai_score", 70).execute().data
    ]

    # 3. Ищем следующую
    phrases = supabase.table("phrases").select("*").eq("topic_id", topic_id).order("order_index").execute().data
    
    next_phrase = None
    for p in phrases:
        if p['id'] not in completed_ids:
            next_phrase = p
            break
    
    if not next_phrase:
        return templates.TemplateResponse("congrats.html", {"request": request, "topic_slug": topic_slug, "ctx": ctx})

    # Выбираем текст вопроса в зависимости от направления
    # Если RU->EN, показываем text_ru. Если EN->UZ, показываем text_en
    question_text = next_phrase.get(f"text_{source_lang.lower()}", "Error text")
    
    # Красивое название целевого языка для заголовка
    target_lang_name = TARGET_LANG_NAMES[ctx["lang"]].get(target_lang, target_lang)

    return templates.TemplateResponse("training.html", {
        "request": request,
        "phrase": next_phrase,
        "question_text": question_text,
        "target_lang_code": target_lang, # EN, RU, UZ
        "target_lang_name": target_lang_name,
        "result": None,
        "topic_slug": topic_slug,
        "ctx": ctx
    })

@app.post("/check", response_class=HTMLResponse)
async def check_answer(
    request: Request,
    phrase_id: int = Form(...),
    original_text: str = Form(...),
    user_translation: str = Form(...),
    target_lang_code: str = Form(...),
    topic_slug: str = Form(...)
):
    ctx = get_user_context(request)
    source_lang, _ = ctx["dir"].split("-")

    # Формируем направление для AI (например "Russian -> English")
    dir_map = {"RU": "Russian", "EN": "English", "UZ": "Uzbek"}
    direction_str = f"{dir_map.get(source_lang)} -> {dir_map.get(target_lang_code)}"

    # Проверка AI
    ai_result = await evaluate_translation(
        original=original_text,
        user_translation=user_translation,
        direction=direction_str,
        interface_lang=ctx["lang"]
    )

    # Сохранение
    try:
        supabase.table("user_attempts").insert({
            "user_id": ctx["user_id"], # Если не логинился - тут анонимный ID, если логинился - реальный
            "phrase_id": phrase_id,
            "direction": ctx["dir"],
            "user_translation": user_translation,
            "ai_score": ai_result['score'],
            "ai_feedback": ai_result['explanation'],
            "ideal_translation": ai_result['ideal_translation']
        }).execute()
    except Exception as e:
        print(f"Save error: {e}")

    # Для повторного отображения вопроса
    target_lang_name = TARGET_LANG_NAMES[ctx["lang"]].get(target_lang_code, target_lang_code)

    return templates.TemplateResponse("training.html", {
        "request": request,
        "phrase": {"id": phrase_id},
        "question_text": original_text,
        "target_lang_code": target_lang_code,
        "target_lang_name": target_lang_name,
        "result": ai_result,
        "user_input": user_translation,
        "topic_slug": topic_slug, # Если тут будет "mistakes", кнопка Next отправит на /mistakes
        "ctx": ctx
    })

@app.get("/mistakes", response_class=HTMLResponse)
async def start_mistakes(request: Request):
    """Режим работы над ошибками"""
    ctx = get_user_context(request)
    source_lang, target_lang = ctx["dir"].split("-")

    # 1. Получаем список ID ошибок
    error_ids = get_error_phrases(ctx["user_id"])

    # --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
    if not error_ids:
        # Если ошибок нет (или закончились), показываем страницу поздравления
        return templates.TemplateResponse("congrats_mistakes.html", {"request": request, "ctx": ctx})
    # -------------------------

    # 2. Берем первую ошибку
    next_phrase_id = error_ids[0]
    
    # 3. Загружаем фразу
    phrase_res = supabase.table("phrases").select("*").eq("id", next_phrase_id).execute()
    
    # Защита от случая, если фразу удалили из базы
    if not phrase_res.data:
        # Если фраза не найдена, рекурсивно пробуем следующую или выходим
        return RedirectResponse("/mistakes")

    next_phrase = phrase_res.data[0]

    question_text = next_phrase.get(f"text_{source_lang.lower()}", "Error text")
    target_lang_name = TARGET_LANG_NAMES[ctx["lang"]].get(target_lang, target_lang)

    return templates.TemplateResponse("training.html", {
        "request": request,
        "phrase": next_phrase,
        "question_text": question_text,
        "target_lang_code": target_lang,
        "target_lang_name": target_lang_name,
        "result": None,
        "topic_slug": "mistakes", # Важно: маркер, что мы в режиме ошибок
        "ctx": ctx
    })


#uvicorn app.main:app --reload