from __future__ import annotations

from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]


class Settings(BaseSettings):
    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    google_service_account_json: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "GOOGLE_SERVICE_ACCOUNT_JSON",
            "SERVICE_ACCOUNT_JSON",
        ),
    )
    google_spreadsheet_id: str | None = Field(
        default=None,
        alias="GOOGLE_SPREADSHEET_ID",
    )
    google_worksheet_name: str = Field(default="Expenses", alias="GOOGLE_WORKSHEET_NAME")
    google_api_timeout_seconds: int = Field(default=30, alias="GOOGLE_API_TIMEOUT_SECONDS")
    openai_compatible_base_url: str = Field(
        default="http://localhost:8080",
        validation_alias=AliasChoices(
            "OPENAI_COMPATIBLE_BASE_URL",
            "LLAMA_CPP_BASE_URL",
            "OLLAMA_BASE_URL",
        ),
    )
    openai_compatible_model: str = Field(
        default="local-model",
        validation_alias=AliasChoices(
            "OPENAI_COMPATIBLE_MODEL",
            "LLAMA_CPP_MODEL",
            "OLLAMA_MODEL",
        ),
    )
    openai_compatible_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPENAI_COMPATIBLE_API_KEY",
            "OPENAI_API_KEY",
        ),
    )
    openai_compatible_reasoning_effort: ReasoningEffort | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPENAI_COMPATIBLE_REASONING_EFFORT",
            "REASONING_EFFORT",
        ),
    )
    request_timeout_seconds: int = Field(default=20, alias="REQUEST_TIMEOUT_SECONDS")
    bot_timezone: str = Field(default="Europe/Moscow", alias="BOT_TIMEZONE")
    test_mode: bool = Field(default=True, alias="TEST_MODE")
    audit_log_path: Path = Field(default=Path("logs/interactions.jsonl"), alias="AUDIT_LOG_PATH")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8-sig",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator(
        "google_service_account_json",
        "google_spreadsheet_id",
        "openai_compatible_api_key",
        "openai_compatible_reasoning_effort",
        mode="before",
    )
    @classmethod
    def empty_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("request_timeout_seconds", "google_api_timeout_seconds")
    @classmethod
    def validate_positive_timeout(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Значение таймаута должно быть больше нуля.")
        return value

    @field_validator("openai_compatible_base_url")
    @classmethod
    def strip_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("bot_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @model_validator(mode="after")
    def validate_google_settings(self) -> "Settings":
        if self.test_mode:
            return self

        if self.google_service_account_json is None:
            raise ValueError(
                "GOOGLE_SERVICE_ACCOUNT_JSON обязателен, если TEST_MODE=false."
            )
        if self.google_spreadsheet_id is None:
            raise ValueError(
                "GOOGLE_SPREADSHEET_ID обязателен, если TEST_MODE=false."
            )
        return self

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.bot_timezone)
