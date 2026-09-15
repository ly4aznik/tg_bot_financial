from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field, field_validator

from expense_bot.categories import ExpenseType


@dataclass(frozen=True)
class RecentExpense:
    expense_date: date
    expense_amount: int
    expense_type: str
    expense_description: str


class ExpenseRecord(BaseModel):
    expense_type: ExpenseType
    expense_date: date
    expense_amount: Decimal
    expense_description: str
    telegram_user_id: int
    telegram_username: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("expense_type", mode="before")
    @classmethod
    def parse_expense_type(cls, value: object) -> ExpenseType:
        if isinstance(value, ExpenseType):
            return value
        if isinstance(value, str):
            return ExpenseType(value)
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
        raise TypeError("Дата траты должна быть датой или ISO-строкой.")

    @field_validator("expense_amount", mode="before")
    @classmethod
    def parse_expense_amount(cls, value: object) -> Decimal:
        if isinstance(value, Decimal):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return Decimal(str(value))
        if isinstance(value, str):
            cleaned = value.replace("\u00a0", "").replace(" ", "")
            if not re.fullmatch(r"\d+", cleaned):
                raise ValueError("Введи положительную целую сумму без копеек, например 650 или 1250.")
            try:
                return Decimal(cleaned)
            except InvalidOperation as exc:
                raise ValueError("Не удалось прочитать сумму.") from exc
        raise TypeError("Сумма должна быть числом.")

    @field_validator("expense_amount")
    @classmethod
    def validate_expense_amount(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("Сумма должна быть больше нуля.")
        if value != value.to_integral_value():
            raise ValueError("Сумма должна быть целым числом без копеек.")
        return value

    @field_validator("expense_description")
    @classmethod
    def validate_expense_description(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if len(cleaned) < 2:
            raise ValueError("Описание должно содержать минимум два символа.")
        return cleaned
