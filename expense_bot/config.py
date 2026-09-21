from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    allowed_telegram_user_ids: str = Field(..., alias="ALLOWED_TELEGRAM_USER_IDS")
    bot_timezone: str = Field(default="Europe/Moscow", alias="BOT_TIMEZONE")
    audit_log_path: Path = Field(default=Path("logs/interactions.jsonl"), alias="AUDIT_LOG_PATH")
    sqlite_database_path: Path = Field(
        default=Path("data/expenses.sqlite3"),
        alias="SQLITE_DATABASE_PATH",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8-sig",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("bot_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("allowed_telegram_user_ids")
    @classmethod
    def validate_allowed_user_ids(cls, value: str) -> str:
        parts = [part.strip() for part in value.split(",") if part.strip()]
        if not parts:
            raise ValueError("ALLOWED_TELEGRAM_USER_IDS must contain at least one Telegram user ID.")
        if any(not part.isdigit() or int(part) <= 0 for part in parts):
            raise ValueError("ALLOWED_TELEGRAM_USER_IDS must be a comma-separated list of positive integers.")
        return ",".join(parts)

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.bot_timezone)

    @property
    def allowed_user_ids(self) -> frozenset[int]:
        return frozenset(int(part) for part in self.allowed_telegram_user_ids.split(","))
