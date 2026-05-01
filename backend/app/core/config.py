from __future__ import annotations

from dotenv import load_dotenv
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENVIRONMENT: str = "development"

    # LLM
    GITHUB_TOKEN: str = Field(default="", repr=False)
    GITHUB_MODEL: str = "gpt-4.1-mini"
    GITHUB_VISION_MODEL: str = "gpt-4.1-mini"
    LLM_TIMEOUT_SECONDS: float = 45

    # Database and queue
    DATABASE_URL: str = "sqlite+aiosqlite:///./vizzy.db"
    REDIS_URL: str = "redis://localhost:6379/0"
    QUEUE_JOB_TIMEOUT_SECONDS: int = 180
    QUEUE_MAX_TRIES: int = 3

    # Image generation
    IMAGE_BACKEND: str = "pollinations"
    HF_TOKEN: str | None = Field(default=None, repr=False)

    # Storage
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = Field(default="", repr=False)
    STORAGE_DIR: str = "./storage"
    MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024
    PUBLIC_BASE_URL: str = "http://localhost:8000"

    # CORS
    FRONTEND_ORIGIN: str = "http://localhost:3000"

    # Server
    PORT: int = 8000

    # Auth
    JWT_SECRET_KEY: str = Field(default="", repr=False)
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 10080
    GUEST_JWT_EXPIRE_HOURS: int = 48

    @field_validator("FRONTEND_ORIGIN")
    @classmethod
    def normalize_frontend_origin(cls, value: str) -> str:
        origins = [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
        return ",".join(origins)

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if self.ENVIRONMENT.lower() in {"production", "prod"}:
            missing = [
                name
                for name in (
                    "DATABASE_URL",
                    "JWT_SECRET_KEY",
                    "GITHUB_TOKEN",
                    "FRONTEND_ORIGIN",
                    "PUBLIC_BASE_URL",
                )
                if not getattr(self, name)
            ]
            if missing:
                joined = ", ".join(missing)
                raise ValueError(f"Missing required production environment variables: {joined}")
            if self.FRONTEND_ORIGIN == "*":
                raise ValueError("FRONTEND_ORIGIN cannot be '*' in production")
            if len(self.JWT_SECRET_KEY) < 32:
                raise ValueError("JWT_SECRET_KEY must be at least 32 characters long")
        return self


settings = Settings()
