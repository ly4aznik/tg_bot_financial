from __future__ import annotations

from datetime import date
from typing import Protocol

from expense_bot.models import ExpenseRecord


class ExpenseRepository(Protocol):
    async def append_expense(self, expense: ExpenseRecord) -> None: ...

    async def summarize_expenses(
        self,
        month_start: date,
        next_month_start: date,
        year_start: date,
        next_year_start: date,
    ) -> dict[str, tuple[int, int]]: ...


class ExpenseService:
    def __init__(self, repository: ExpenseRepository) -> None:
        self._repository = repository

    async def persist_expense(self, expense: ExpenseRecord) -> None:
        await self._repository.append_expense(expense)

    async def summarize_expenses(
        self,
        month_start: date,
        next_month_start: date,
        year_start: date,
        next_year_start: date,
    ) -> dict[str, tuple[int, int]]:
        return await self._repository.summarize_expenses(
            month_start,
            next_month_start,
            year_start,
            next_year_start,
        )
