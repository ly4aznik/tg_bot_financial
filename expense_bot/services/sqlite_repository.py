from __future__ import annotations

import asyncio
import sqlite3
from datetime import date
from pathlib import Path

from expense_bot.models import ExpenseRecord, RecentExpense


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
                FROM expenses
                ORDER BY id DESC
                LIMIT ?
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
