from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from pydantic import BaseModel, Field, field_validator

from expense_bot.categories import ExpenseType, resolve_expense_type


class Expense(BaseModel):
    expense_type: ExpenseType
    expense_date: date
    expense_amount: Decimal
    expense_description: str

    @field_validator("expense_type", mode="before")
    @classmethod
    def parse_expense_type(cls, value: object) -> ExpenseType:
        if isinstance(value, ExpenseType):
            return value
        if isinstance(value, str):
            return resolve_expense_type(value)
        raise TypeError("Тип траты должен быть строкой или ExpenseType.")

    @field_validator("expense_date", mode="before")
    @classmethod
    def parse_expense_date(cls, value: object) -> date:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            return date.fromisoformat(value)
        raise TypeError("Дата траты должна быть date, datetime или ISO-строкой.")

    @field_validator("expense_amount", mode="before")
    @classmethod
    def parse_expense_amount(cls, value: object) -> Decimal:
        if isinstance(value, Decimal):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return Decimal(str(value))
        if isinstance(value, str):
            cleaned = value.replace("\u00a0", "").replace(" ", "").replace(",", ".")
            cleaned = re.sub(r"[^0-9.\-]", "", cleaned)
            if not cleaned:
                raise ValueError("Не удалось распознать сумму траты.")
            try:
                return Decimal(cleaned)
            except InvalidOperation as exc:
                raise ValueError("Не удалось распознать сумму траты.") from exc
        raise TypeError("Сумма траты должна быть числом или строкой.")

    @field_validator("expense_amount")
    @classmethod
    def validate_expense_amount(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("Сумма траты должна быть больше нуля.")
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @field_validator("expense_description")
    @classmethod
    def validate_expense_description(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("Описание траты слишком короткое.")
        return cleaned


class ParsedExpense(Expense):
    pass


class LLMUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None


class LLMCallMetrics(BaseModel):
    model: str
    latency_ms: int
    reasoning_effort: str | None = None
    usage: LLMUsage = Field(default_factory=LLMUsage)


class ExpenseRecord(Expense):
    raw_text: str
    telegram_user_id: int
    telegram_username: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("raw_text")
    @classmethod
    def validate_raw_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Исходный текст сообщения не может быть пустым.")
        return cleaned

    @field_validator("created_at", mode="before")
    @classmethod
    def validate_created_at(cls, value: object) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("created_at должен быть ISO-datetime.") from exc
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        raise ValueError("created_at должен быть datetime.")

    @property
    def sheet_row(self) -> list[str]:
        return [
            self.created_at.astimezone(timezone.utc).isoformat(),
            self.expense_date.isoformat(),
            f"{self.expense_amount:.2f}",
            self.expense_type.value,
            self.expense_description,
            self.raw_text,
            str(self.telegram_user_id),
            self.telegram_username or "",
        ]


class ParsedExpenseResult(BaseModel):
    expense: ParsedExpense
    llm_call: LLMCallMetrics


class ExpenseRecognitionResult(BaseModel):
    expense: ExpenseRecord
    llm_call: LLMCallMetrics
