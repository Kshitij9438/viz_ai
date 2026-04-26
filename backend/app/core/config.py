import os

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM provider (GitHub Models / Azure OpenAI-compatible)
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
    GITHUB_MODEL: str = "gpt-4.1-mini"
    GITHUB_VISION_MODEL: str = "gpt-4.1-mini"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./vizzy.db"

    # Image generation backend
    IMAGE_BACKEND: str = "pollinations"
    HF_TOKEN: str | None = None

    # Storage & public URL
    STORAGE_DIR: str = "./storage"
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")

    # CORS (comma-separated for multiple origins)
    FRONTEND_ORIGIN: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")

    # Railway injects PORT automatically
    PORT: int = int(os.environ.get("PORT", "8000"))


settings = Settings()
