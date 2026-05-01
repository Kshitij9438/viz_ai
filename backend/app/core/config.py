import os

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---------------------------
    # 🔐 LLM
    # ---------------------------
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
    GITHUB_MODEL: str = "gpt-4.1-mini"
    GITHUB_VISION_MODEL: str = "gpt-4.1-mini"

    # ---------------------------
    # 🗄️ DATABASE
    # ---------------------------
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    # ---------------------------
    # 🧠 IMAGE BACKEND
    # ---------------------------
    IMAGE_BACKEND: str = "pollinations"
    HF_TOKEN: str | None = None

    # ---------------------------
    # ☁️ SUPABASE STORAGE (NEW)
    # ---------------------------
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
    SUPABASE_BUCKET: str = os.getenv("SUPABASE_BUCKET", "")

    # ---------------------------
    # 🌍 PUBLIC URL (for assets)
    # ---------------------------
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "")

    # ---------------------------
    # 📁 LOCAL STORAGE (DEPRECATED)
    # ---------------------------
    STORAGE_DIR: str = "./storage"

    # ---------------------------
    # 🌐 CORS
    # ---------------------------
    FRONTEND_ORIGIN: str = os.getenv("FRONTEND_ORIGIN", "")

    # ---------------------------
    # 🚀 SERVER
    # ---------------------------
    PORT: int = int(os.environ.get("PORT", "8000"))

    # ---------------------------
    # 🔐 AUTH
    # ---------------------------
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = int(os.environ.get("JWT_EXPIRE_MINUTES", "10080"))  # 7 days
    GUEST_JWT_EXPIRE_HOURS: int = int(os.environ.get("GUEST_JWT_EXPIRE_HOURS", "48"))


settings = Settings()