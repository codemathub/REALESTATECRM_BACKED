from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BASE_DIR / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    ENVIRONMENT: str = "development"
    PORT: int = 8000
    HOST: str = "0.0.0.0"
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "estateflow CRM API"

    # MongoDB Atlas
    MONGODB_URI: str
    MONGODB_DB_NAME: str = "estateflow_crm"

    # JWT Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours (prevents sudden 15-minute credential expiration during admin work)
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Initial Super Admin (Loaded strictly from .env)
    AUTHORIZED_ADMIN_EMAIL: str | None = None
    INITIAL_SUPER_ADMIN_USERNAME: str | None = None
    INITIAL_SUPER_ADMIN_PASSWORD: str | None = None

    # Resend Transactional Email
    RESEND_API_KEY: str | None = None
    RESEND_FROM_EMAIL: str = "Mad Ad Theory <onboarding@resend.dev>"
    WEB_URL: str = "http://localhost:5173"
    MOBILE_SCHEME: str = "estateflow://activate"

    # Localization
    COMPANY_TIMEZONE: str = "Asia/Kolkata"

    # CORS
    CORS_ORIGINS: str | None = None

    @property
    def allowed_origins_list(self) -> List[str]:
        if self.CORS_ORIGINS:
            return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]
        return [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8081",
            "exp://localhost:8081",
        ]


settings = Settings()
