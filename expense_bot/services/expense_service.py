from __future__ import annotations

import calendar
from datetime import date
from typing import Protocol

from expense_bot.models import ExpenseRecord, ExpenseTrend, RecentExpense


def shift_month(month_start: date, months: int) -> date:
    month_index = month_start.year * 12 + month_start.month - 1 + months
    year, zero_based_month = divmod(month_index, 12)
    return date(year, zero_based_month + 1, 1)


class ExpenseRepository(Protocol):
    async def append_expense(self, expense: ExpenseRecord) -> None: ...

    async def summarize_expenses(
        self,
        month_start: date,
        next_month_start: date,
        year_start: date,
        next_year_start: date,
    ) -> dict[str, tuple[int, int]]: ...

    async def list_recent_expenses(self, limit: int) -> list[RecentExpense]: ...

    async def daily_expense_totals(self, start: date, end: date) -> dict[date, int]: ...


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

    async def list_recent_expenses(self, limit: int = 10) -> list[RecentExpense]:
        return await self._repository.list_recent_expenses(limit)

    async def build_expense_trend(self, today: date) -> ExpenseTrend:
        current_start = today.replace(day=1)
        next_month_start = shift_month(current_start, 1)
        history_start = shift_month(current_start, -12)
        totals = await self._repository.daily_expense_totals(history_start, next_month_start)
        day_count = calendar.monthrange(today.year, today.month)[1]
        days = list(range(1, day_count + 1))

        current_cumulative: list[int | None] = []
        running_total = 0
        for day in days:
            if day > today.day:
                current_cumulative.append(None)
                continue
            running_total += totals.get(date(today.year, today.month, day), 0)
            current_cumulative.append(running_total)

        history_series: list[list[int]] = []
        for month_offset in range(-12, 0):
            month_start = shift_month(current_start, month_offset)
            days_in_month = calendar.monthrange(month_start.year, month_start.month)[1]
            cumulative = []
            month_total = 0
            for day in range(1, days_in_month + 1):
                month_total += totals.get(date(month_start.year, month_start.month, day), 0)
                cumulative.append(month_total)
            history_series.append([
                cumulative[min(day, days_in_month) - 1]
                for day in days
            ])

        average_cumulative = [
            sum(series[index] for series in history_series) / len(history_series)
            for index in range(day_count)
        ]
        return ExpenseTrend(
            days=days,
            current_cumulative=current_cumulative,
            average_cumulative=average_cumulative,
            today_day=today.day,
            current_month_label=current_start.strftime("%m.%Y"),
            history_label=f"{history_start:%m.%Y}–{shift_month(current_start, -1):%m.%Y}",
        )
