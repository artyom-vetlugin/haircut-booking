from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_env: str = "development"
    debug: bool = False

    # Database — must use postgresql+asyncpg:// scheme
    database_url: str

    # Telegram
    telegram_bot_token: str
    telegram_master_chat_id: int
    telegram_webhook_secret: str = ""
    telegram_webhook_url: str = ""

    # Anthropic Claude (populated when Claude integration is added)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # Google Calendar (populated when calendar integration is added)
    google_calendar_id: str = ""

    # Booking rules — all configurable via environment
    booking_horizon_days: int = 30
    min_notice_hours: int = 2
    working_hours_start: int = 9   # 09:00 local time
    working_hours_end: int = 19    # 19:00 local time
    appointment_duration_minutes: int = 60

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if not v.startswith("postgresql"):
            raise ValueError("DATABASE_URL must be a PostgreSQL connection string")
        return v


settings = Settings()
