from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    SUPABASE_URL: str
    SUPABASE_KEY: str
    GEMINI_API_KEY: str
    LLAMA_API_KEY: str = ""
    LLAMA_BASE_URL: str = ""
    LLAMA_MODEL_NAME: str = "llama-3.3-70b-versatile"


    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()