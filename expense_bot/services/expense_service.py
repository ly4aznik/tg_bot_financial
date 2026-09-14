from __future__ import annotations

from typing import Protocol

from expense_bot.models import ExpenseRecord


class ExpenseRepository(Protocol):
    async def append_expense(self, expense: ExpenseRecord) -> None: ...


class ExpenseService:
    def __init__(self, repository: ExpenseRepository) -> None:
        self._repository = repository

    async def persist_expense(self, expense: ExpenseRecord) -> None:
        await self._repository.append_expense(expense)
