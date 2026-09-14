import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from expense_bot.categories import ExpenseType
from expense_bot.models import ExpenseRecord
from expense_bot.services.sqlite_repository import SQLiteExpenseRepository


@pytest.mark.asyncio
async def test_sqlite_repository_creates_database_and_saves_expense(tmp_path) -> None:
    database_path = tmp_path / "nested" / "expenses.sqlite3"
    repository = SQLiteExpenseRepository(database_path)
    expense = ExpenseRecord(
        expense_type=ExpenseType.TRANSPORT,
        expense_date=date(2026, 9, 14),
        expense_amount=Decimal("650"),
        expense_description="Такси домой",
        telegram_user_id=42,
        telegram_username="tester",
    )

    await repository.append_expense(expense)

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT expense_date, expense_amount, expense_type,
                   expense_description, telegram_user_id, telegram_username,
                   source_file, source_row
            FROM expenses
            """
        ).fetchone()

    assert row == (
        "2026-09-14",
        650,
        "Транспорт",
        "Такси домой",
        42,
        "tester",
        None,
        None,
    )


def test_sqlite_repository_initialization_is_idempotent(tmp_path) -> None:
    database_path = tmp_path / "expenses.sqlite3"
    SQLiteExpenseRepository(database_path)
    SQLiteExpenseRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        table_count = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'expenses'"
        ).fetchone()[0]

    assert table_count == 1
