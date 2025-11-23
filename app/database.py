from supabase import create_client, Client
from app.config import settings

# Стандартное подключение
supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)