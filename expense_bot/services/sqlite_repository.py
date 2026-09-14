from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from expense_bot.models import ExpenseRecord


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
