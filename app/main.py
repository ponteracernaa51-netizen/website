import uuid
import re
import pandas as pd
import io
from fastapi import FastAPI, Request, Form, Response, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse # <-- Вот здесь было изменение
from fastapi.templating import Jinja2Templates
from app.database import supabase
from app.ai_service import evaluate_translation
from app.translations import UI_TEXTS, TARGET_LANG_NAMES
from fastapi import UploadFile, File
from fastapi import FastAPI, Form
from app.ai_service import evaluate_translation

app = FastAPI(title="FluentEdgeAI")
templates = Jinja2Templates(directory="templates")

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_user_context(request: Request):
    # ... (старый код получения user_id, lang, dir) ...
    user_id = request.cookies.get("fluent_user_id")
    if not user_id:
        user_id = str(uuid.uuid4())
    lang = request.cookies.get("fluent_lang", "ru")
    direction = request.cookies.get("fluent_dir", "RU-EN")
    is_auth = request.cookies.get("fluent_is_auth") == "true"
    
    # --- ДОБАВЛЕНО: Проверка админа (упрощенная) ---
    # Чтобы не делать запрос к БД при каждом клике, можно пока просто вернуть False,
    # а в /admin мы проверяем строго. Но для кнопки в меню сделаем запрос:
    is_admin = False
    if user_id and is_auth:
         try:
            # В реальном проекте лучше кэшировать это в куки, чтобы не нагружать базу
            res = supabase.table("profiles").select("is_admin").eq("id", user_id).execute()
            if res.data and res.data[0]['is_admin']:
                is_admin = True
         except:
             pass
    # -----------------------------------------------

    return {
        "user_id": user_id,
        "lang": lang,
        "dir": direction,
        "is_auth": is_auth,
        "is_admin": is_admin, # <--- Не забудь добавить это в return
        "ui": UI_TEXTS.get(lang, UI_TEXTS["ru"])
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
        supabase.table("user_attempts").select("phrase_id").eq("user_id", ctx["user_id"]).gt("ai_score", 40).execute().data
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
    
    # 1. ПОЛУЧАЕМ ФРАЗУ ИЗ БАЗЫ ДАННЫХ
    # Нам нужно достать "эталонный" перевод, которого нет в форме
    try:
        response = supabase.table("phrases").select("*").eq("id", phrase_id).execute()
        if not response.data:
            raise ValueError("Phrase not found")
        phrase_data = response.data[0]
    except Exception as e:
        print(f"DB Error: {e}")
        return HTMLResponse("Error fetching phrase", status_code=500)

    # 2. ОПРЕДЕЛЯЕМ ЭТАЛОН (Reference)
    # ctx["dir"] выглядит как "ru-en", "en-uz" и т.д.
    # Нам нужно понять, на какой язык переводим, чтобы взять нужное поле из базы.
    
    target = target_lang_code.lower() # en, ru, или uz
    reference_text = ""

    if target == "en":
        reference_text = phrase_data.get("text_en", "")
    elif target == "ru":
        reference_text = phrase_data.get("text_ru", "")
    elif target == "uz":
        reference_text = phrase_data.get("text_uz", "")
    
    # Страховка, если вдруг поле пустое
    if not reference_text:
        reference_text = "Translation missing in database"

    # 3. ПРОВЕРКА AI (С НОВЫМ АРГУМЕНТОМ)
    ai_result = await evaluate_translation(
        original=original_text,
        reference_translation=reference_text, # <--- ПЕРЕДАЕМ ЭТАЛОН
        user_translation=user_translation,
        direction=ctx["dir"], # Лучше передавать короткий код, например "ru-en"
        interface_lang=ctx["lang"]
    )

    # Сохранение (без изменений)
    try:
        supabase.table("user_attempts").insert({
            "user_id": ctx["user_id"], 
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
    target_lang_name = TARGET_LANG_NAMES.get(ctx["lang"], {}).get(target_lang_code, target_lang_code)

    return templates.TemplateResponse("training.html", {
        "request": request,
        "phrase": {"id": phrase_id},
        "question_text": original_text,
        "target_lang_code": target_lang_code,
        "target_lang_name": target_lang_name,
        "result": ai_result,
        "user_input": user_translation,
        "topic_slug": topic_slug,
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

# --- ADMIN PANEL ---

async def check_admin(request: Request):
    """Проверяем, является ли текущий пользователь админом"""
    user_id = request.cookies.get("fluent_user_id")
    if not user_id:
        return False
    
    try:
        # Запрашиваем поле is_admin из таблицы profiles
        res = supabase.table("profiles").select("is_admin").eq("id", user_id).execute()
        if res.data and res.data[0]['is_admin'] == True:
            return True
    except Exception as e:
        print(f"Admin check error: {e}")
    
    return False

@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    if not await check_admin(request): return RedirectResponse("/", status_code=302)
    ctx = get_user_context(request)

    # Загружаем данные
    topics = supabase.table("topics").select("*").order("id").execute().data
    levels = supabase.table("levels").select("id, slug").execute().data
    phrases = supabase.table("phrases").select("topic_id").execute().data
    
    # НОВОЕ: Загружаем пользователей (последние 50)
    users = supabase.table("profiles").select("*").order("created_at", desc=True).limit(50).execute().data

    # ... (код с lvl_map и enriched_topics остается тем же) ...
    # Просто скопируй старую логику обогащения тем сюда
    lvl_map = {l['id']: l['slug'].upper() for l in levels}
    phrase_counts = {}
    for p in phrases:
        tid = p['topic_id']
        phrase_counts[tid] = phrase_counts.get(tid, 0) + 1
    enriched_topics = []
    for t in topics:
        t['level_slug'] = lvl_map.get(t.get('level_id'), '??')
        t['count'] = phrase_counts.get(t['id'], 0)
        enriched_topics.append(t)

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "ctx": ctx,
        "topics": enriched_topics,
        "users": users # <--- Передаем пользователей в шаблон
    })

@app.post("/admin/add_phrase")
async def admin_add_phrase(
    request: Request,
    topic_id: int = Form(...),
    text_ru: str = Form(...),
    text_en: str = Form(...),
    text_uz: str = Form(...),
    order_index: int = Form(...)
):
    """Обработчик добавления фразы"""
    
    if not await check_admin(request):
        return "Access Denied"

    try:
        supabase.table("phrases").insert({
            "topic_id": topic_id,
            "text_ru": text_ru,
            "text_en": text_en,
            "text_uz": text_uz,
            "order_index": order_index
        }).execute()
    except Exception as e:
        return f"Error adding phrase: {e}"

    # Возвращаемся в админку
    return RedirectResponse("/admin", status_code=302)

# --- УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ ---

@app.post("/admin/toggle_admin")
async def admin_toggle_user(request: Request, user_id: str = Form(...), is_admin: str = Form(...)):
    """Переключатель прав администратора (Исправленная версия)"""
    
    # Проверка прав (только админ может назначать админов)
    if not await check_admin(request): 
        return "Access Denied"
    
    # 1. Конвертируем строку из формы в Python-булево
    # HTML передает "True" или "False" как текст
    current_status = (is_admin == "True")
    
    # 2. Меняем статус на противоположный
    new_status = not current_status
    
    print(f"🔄 Смена прав для {user_id}: {current_status} -> {new_status}")

    try:
        supabase.table("profiles").update({"is_admin": new_status}).eq("id", user_id).execute()
    except Exception as e:
        print(f"❌ Ошибка смены прав: {e}")
        return f"Error: {e}"

    return RedirectResponse("/admin", status_code=302)

@app.post("/admin/delete_user")
async def admin_delete_user(request: Request, user_id: str = Form(...)):
    if not await check_admin(request): return "Access Denied"
    
    # Удаляем профиль (авторизация Supabase останется, но вход на сайт перестанет работать)
    supabase.table("profiles").delete().eq("id", user_id).execute()
    return RedirectResponse("/admin", status_code=302)

# --- УПРАВЛЕНИЕ КОНТЕНТОМ (УДАЛЕНИЕ) ---

@app.post("/admin/delete_topic")
async def admin_delete_topic(request: Request, topic_id: int = Form(...)):
    if not await check_admin(request): return "Access Denied"
    
    print(f"🗑 Удаление темы ID: {topic_id}")
    
    try:
        # Благодаря SQL скрипту выше, это удалит и тему, и фразы
        supabase.table("topics").delete().eq("id", topic_id).execute()
    except Exception as e:
        print(f"❌ Ошибка удаления темы: {e}")
        return f"Database Error: {e}"

    return RedirectResponse("/admin", status_code=302)

@app.get("/admin/topic/{topic_id}", response_class=HTMLResponse)
async def admin_topic_details(request: Request, topic_id: int):
    """Страница управления фразами конкретной темы"""
    if not await check_admin(request): return RedirectResponse("/", status_code=302)
    
    ctx = get_user_context(request)
    
    # Получаем тему и фразы
    topic = supabase.table("topics").select("*").eq("id", topic_id).single().execute().data
    phrases = supabase.table("phrases").select("*").eq("topic_id", topic_id).order("order_index").execute().data

    return templates.TemplateResponse("admin_topic.html", {
        "request": request,
        "ctx": ctx,
        "topic": topic,
        "phrases": phrases
    })

@app.post("/admin/delete_phrase")
async def admin_delete_phrase(request: Request, phrase_id: int = Form(...), topic_id: int = Form(...)):
    if not await check_admin(request): return "Access Denied"
    
    supabase.table("phrases").delete().eq("id", phrase_id).execute()
    # Возвращаем обратно на страницу темы
    return RedirectResponse(f"/admin/topic/{topic_id}", status_code=302)

@app.post("/admin/import_excel")
async def admin_import_excel(
    request: Request,
    topic_id: int = Form(...),
    file: UploadFile = File(...)
):
    """Импорт фраз из Excel файла"""
    
    # 1. Проверка админа
    if not await check_admin(request): 
        return "Access Denied"

    try:
        # 2. Читаем файл в Pandas DataFrame
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))
        
        # 3. Приводим названия колонок к нижнему регистру (на всякий случай)
        df.columns = [c.lower().strip() for c in df.columns]

        # 4. Проверяем, есть ли нужные колонки
        required_cols = ['text_ru', 'text_en', 'text_uz']
        for col in required_cols:
            if col not in df.columns:
                return f"Ошибка: В Excel файле нет колонки '{col}'"

        # 5. Подготовка данных для Supabase
        phrases_to_insert = []
        
        # Определяем начальный order_index (чтобы добавлять в конец)
        # Если в Excel есть колонка order_index, используем её, иначе авто
        has_order = 'order_index' in df.columns
        
        current_index = 1
        # Можно сделать запрос к БД, чтобы узнать последний индекс, но для простоты начнем с 1
        
        for _, row in df.iterrows():
            phrase_data = {
                "topic_id": topic_id,
                "text_ru": str(row['text_ru']),
                "text_en": str(row['text_en']),
                "text_uz": str(row['text_uz']),
                "order_index": int(row['order_index']) if has_order else current_index
            }
            phrases_to_insert.append(phrase_data)
            current_index += 1

        # 6. Массовая вставка в базу (Bulk Insert)
        if phrases_to_insert:
            supabase.table("phrases").insert(phrases_to_insert).execute()
            print(f"✅ Успешно импортировано {len(phrases_to_insert)} фраз.")

    except Exception as e:
        print(f"❌ Ошибка импорта: {e}")
        return f"Error importing file: {e}"

    return RedirectResponse("/admin", status_code=302)

#uvicorn app.main:app --reload
#venv\Scripts\Activate.ps1
