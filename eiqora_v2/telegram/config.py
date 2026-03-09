"""Telegram bot configuration."""

from pydantic_settings import BaseSettings


class TelegramSettings(BaseSettings):
    telegram_bot_token: str = ""
    database_url: str = "postgresql://postgres:postgres@localhost:5432/finance"

    # Rate limiting (Telegram allows 30 msg/sec, we use 25 for safety)
    broadcast_rate_limit: float = 25.0

    # Scheduled PDF broadcast (SGT)
    summary_hour: int = 22
    summary_minute: int = 0
    summary_timezone: str = "Asia/Singapore"

    # Directory to look for PDFs (relative to bot.py or absolute)
    pdf_dir: str = ""
    pdf_pattern: str = "*.pdf"

    # Allowed Telegram usernames (comma-separated, without @)
    # Leave empty to allow anyone
    allowed_usernames: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = TelegramSettings()
