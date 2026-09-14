from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
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

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.bot_timezone)
