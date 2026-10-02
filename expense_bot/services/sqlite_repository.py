from __future__ import annotations

import asyncio
import sqlite3
from datetime import date
from pathlib import Path

from expense_bot.models import ExpenseRecord, RecentExpense, SavedExpense


class SQLiteExpenseRepository:
    """Persist confirmed expenses in a local SQLite database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @property
    def database_path(self) -> Path:
        return self._database_path

    async def append_expense(self, expense: ExpenseRecord) -> None:
        await asyncio.to_thread(self._append_expense_sync, expense)

    async def summarize_expenses(
        self,
        month_start: date,
        next_month_start: date,
        year_start: date,
        next_year_start: date,
    ) -> dict[str, tuple[int, int]]:
        return await asyncio.to_thread(
            self._summarize_expenses_sync,
            month_start,
            next_month_start,
            year_start,
            next_year_start,
        )

    async def list_recent_expenses(self, limit: int) -> list[RecentExpense]:
        return await asyncio.to_thread(self._list_recent_expenses_sync, limit)

    async def get_last_expense(self) -> SavedExpense | None:
        return await asyncio.to_thread(self._get_last_expense_sync)

    async def delete_last_expense(self, expected: SavedExpense) -> bool:
        return await asyncio.to_thread(self._delete_last_expense_sync, expected)

    async def update_last_expense(self, expected: SavedExpense, expense: ExpenseRecord) -> bool:
        return await asyncio.to_thread(self._update_last_expense_sync, expected, expense)

    def _get_last_expense_sync(self) -> SavedExpense | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT expense_date, expense_amount, expense_type, expense_description, id "
                "FROM expenses ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return SavedExpense(date.fromisoformat(row[0]), int(row[1]), row[2], row[3], row[4])

    @staticmethod
    def _expected_values(expected: SavedExpense) -> tuple:
        return (
            expected.id, expected.expense_date.isoformat(), expected.expense_amount,
            expected.expense_type, expected.expense_description,
        )

    def _delete_last_expense_sync(self, expected: SavedExpense) -> bool:
        # A single statement checks and deletes atomically, even with concurrent writers.
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM expenses WHERE id = ? AND id = (SELECT MAX(id) FROM expenses) "
                "AND expense_date = ? AND expense_amount = ? AND expense_type = ? AND expense_description = ?",
                self._expected_values(expected),
            )
            return cursor.rowcount == 1

    def _update_last_expense_sync(self, expected: SavedExpense, expense: ExpenseRecord) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE expenses SET expense_date = ?, expense_amount = ?, expense_type = ?, expense_description = ? "
                "WHERE id = ? AND id = (SELECT MAX(id) FROM expenses) "
                "AND expense_date = ? AND expense_amount = ? AND expense_type = ? AND expense_description = ?",
                (
                    expense.expense_date.isoformat(), int(expense.expense_amount),
                    expense.expense_type.value, expense.expense_description,
                    *self._expected_values(expected),
                ),
            )
            return cursor.rowcount == 1

    async def daily_expense_totals(self, start: date, end: date) -> dict[date, int]:
        return await asyncio.to_thread(self._daily_expense_totals_sync, start, end)

    async def category_expense_totals(self, start: date, end: date) -> dict[str, int]:
        return await asyncio.to_thread(self._category_expense_totals_sync, start, end)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=30)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    expense_date TEXT NOT NULL,
                    expense_amount INTEGER NOT NULL CHECK (expense_amount > 0),
                    expense_type TEXT NOT NULL,
                    expense_description TEXT NOT NULL,
                    telegram_user_id INTEGER NOT NULL,
                    telegram_username TEXT,
                    created_at TEXT NOT NULL,
                    source_file TEXT,
                    source_row INTEGER,
                    UNIQUE (source_file, source_row)
                )
                """
            )

    def _append_expense_sync(self, expense: ExpenseRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO expenses (
                    expense_date,
                    expense_amount,
                    expense_type,
                    expense_description,
                    telegram_user_id,
                    telegram_username,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    expense.expense_date.isoformat(),
                    int(expense.expense_amount),
                    expense.expense_type.value,
                    expense.expense_description,
                    expense.telegram_user_id,
                    expense.telegram_username,
                    expense.created_at.isoformat(),
                ),
            )

    def _summarize_expenses_sync(
        self,
        month_start: date,
        next_month_start: date,
        year_start: date,
        next_year_start: date,
    ) -> dict[str, tuple[int, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    expense_type,
                    SUM(CASE
                        WHEN expense_date >= ? AND expense_date < ?
                        THEN expense_amount ELSE 0
                    END) AS month_total,
                    SUM(CASE
                        WHEN expense_date >= ? AND expense_date < ?
                        THEN expense_amount ELSE 0
                    END) AS year_total
                FROM expenses
                WHERE expense_date >= ? AND expense_date < ?
                GROUP BY expense_type
                """,
                (
                    month_start.isoformat(),
                    next_month_start.isoformat(),
                    year_start.isoformat(),
                    next_year_start.isoformat(),
                    year_start.isoformat(),
                    next_year_start.isoformat(),
                ),
            ).fetchall()
        return {
            category: (int(month_total), int(year_total))
            for category, month_total, year_total in rows
        }

    def _list_recent_expenses_sync(self, limit: int) -> list[RecentExpense]:
        if limit <= 0:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT expense_date, expense_amount, expense_type, expense_description
                FROM (
                    SELECT id, expense_date, expense_amount, expense_type, expense_description
                    FROM expenses
                    ORDER BY id DESC
                    LIMIT ?
                ) AS recent_expenses
                ORDER BY id ASC
                """,
                (limit,),
            ).fetchall()
        return [
            RecentExpense(
                expense_date=date.fromisoformat(expense_date),
                expense_amount=int(expense_amount),
                expense_type=expense_type,
                expense_description=expense_description,
            )
            for expense_date, expense_amount, expense_type, expense_description in rows
        ]

    def _daily_expense_totals_sync(self, start: date, end: date) -> dict[date, int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT expense_date, SUM(expense_amount)
                FROM expenses
                WHERE expense_date >= ? AND expense_date < ?
                GROUP BY expense_date
                ORDER BY expense_date
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return {
            date.fromisoformat(expense_date): int(total)
            for expense_date, total in rows
        }

    def _category_expense_totals_sync(self, start: date, end: date) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT expense_type, SUM(expense_amount)
                FROM expenses
                WHERE expense_date >= ? AND expense_date < ?
                GROUP BY expense_type
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return {category: int(total) for category, total in rows}
