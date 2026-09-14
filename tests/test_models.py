from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from expense_bot.bot import parse_manual_date
from expense_bot.models import ExpenseRecord


def make_record(**overrides: object) -> ExpenseRecord:
    values = {
        "expense_type": "Транспорт",
        "expense_date": "2026-09-04",
        "expense_amount": "650",
        "expense_description": "Такси",
        "telegram_user_id": 42,
    }
    values.update(overrides)
    return ExpenseRecord.model_validate(values)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("650", Decimal("650")), ("1 250", Decimal("1250")), ("1", Decimal("1"))],
)
def test_amount_validation(raw: str, expected: Decimal) -> None:
    assert make_record(expense_amount=raw).expense_amount == expected


@pytest.mark.parametrize("raw", ["0", "-5", "12.5", "12,50", "abc", ""])
def test_invalid_amount_keeps_validation_strict(raw: str) -> None:
    with pytest.raises(ValidationError):
        make_record(expense_amount=raw)


def test_manual_date_uses_supplied_current_year() -> None:
    assert parse_manual_date("31.12", 2026) == date(2026, 12, 31)


@pytest.mark.parametrize("raw", ["31.02", "1/2", "2026-02-01", ""])
def test_invalid_manual_date(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_manual_date(raw, 2026)


def test_description_is_required() -> None:
    with pytest.raises(ValidationError):
        make_record(expense_description=" ")
