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
        "такси домой",
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


@pytest.mark.asyncio
async def test_sqlite_repository_summarizes_month_and_year(tmp_path) -> None:
    repository = SQLiteExpenseRepository(tmp_path / "expenses.sqlite3")
    for expense_date, amount, category in (
        (date(2026, 9, 1), 100, ExpenseType.TRANSPORT),
        (date(2026, 9, 14), 250, ExpenseType.TRANSPORT),
        (date(2026, 8, 1), 500, ExpenseType.TRANSPORT),
        (date(2026, 9, 3), 80, ExpenseType.LUNCH),
        (date(2025, 9, 3), 999, ExpenseType.LUNCH),
    ):
        await repository.append_expense(
            ExpenseRecord(
                expense_type=category,
                expense_date=expense_date,
                expense_amount=Decimal(amount),
                expense_description="Тест",
                telegram_user_id=42,
            )
        )

    summary = await repository.summarize_expenses(
        date(2026, 9, 1),
        date(2026, 10, 1),
        date(2026, 1, 1),
        date(2027, 1, 1),
    )

    assert summary["Транспорт"] == (350, 850)
    assert summary["Обед"] == (80, 80)


@pytest.mark.asyncio
async def test_sqlite_repository_lists_latest_expenses_oldest_to_newest(tmp_path) -> None:
    repository = SQLiteExpenseRepository(tmp_path / "expenses.sqlite3")
    for day, amount in ((1, 100), (2, 200), (3, 300)):
        await repository.append_expense(
            ExpenseRecord(
                expense_type=ExpenseType.TRANSPORT,
                expense_date=date(2026, 9, day),
                expense_amount=Decimal(amount),
                expense_description=f"Трата {day}",
                telegram_user_id=42,
            )
        )

    recent = await repository.list_recent_expenses(2)

    assert [expense.expense_amount for expense in recent] == [200, 300]
    assert [expense.expense_description for expense in recent] == ["трата 2", "трата 3"]


@pytest.mark.asyncio
async def test_sqlite_repository_groups_daily_expense_totals(tmp_path) -> None:
    repository = SQLiteExpenseRepository(tmp_path / "expenses.sqlite3")
    for expense_date, amount in (
        (date(2026, 9, 1), 100),
        (date(2026, 9, 1), 250),
        (date(2026, 9, 2), 500),
        (date(2026, 10, 1), 999),
    ):
        await repository.append_expense(
            ExpenseRecord(
                expense_type=ExpenseType.OTHER,
                expense_date=expense_date,
                expense_amount=Decimal(amount),
                expense_description="тест",
                telegram_user_id=42,
            )
        )

    totals = await repository.daily_expense_totals(date(2026, 9, 1), date(2026, 10, 1))

    assert totals == {date(2026, 9, 1): 350, date(2026, 9, 2): 500}
