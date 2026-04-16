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

    # Google Calendar — google_calendar_id is the dedicated appointments calendar.
    # google_oauth_credentials_path is the absolute path to the gcp-oauth.keys.json
    # file downloaded from Google Cloud Console (Desktop app credentials).
    # Run the one-time auth flow before starting the app:
    #   GOOGLE_OAUTH_CREDENTIALS=/path/to/keys.json npx @cocal/google-calendar-mcp auth
    google_calendar_id: str = ""
    google_oauth_credentials_path: str = ""

    # Master contact info — shown to clients who ask how to reach the master
    master_contact_phone: str = ""

    # Booking rules — all configurable via environment
    booking_horizon_days: int = 30
    min_notice_hours: int = 2
    working_hours_start: int = 9   # 09:00 local time
    working_hours_end: int = 19    # 19:00 local time
    appointment_duration_minutes: int = 60
    # 0=Mon … 6=Sun; set via JSON env var e.g. WORKING_DAYS='[0,1,2,3,4,5]'
    working_days: list[int] = [0, 1, 2, 3, 4, 5]  # Mon–Sat
    app_timezone: str = "Asia/Almaty"

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if not v.startswith("postgresql"):
            raise ValueError("DATABASE_URL must be a PostgreSQL connection string")
        return v


settings = Settings()
