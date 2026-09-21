from datetime import date

import pytest

from expense_bot.models import ExpenseTrend
from expense_bot.services.expense_service import ExpenseService, shift_month
from expense_bot.trend_chart import render_expense_trend


class DailyTotalsRepository:
    def __init__(self, totals: dict[date, int]) -> None:
        self.totals = totals
        self.requested_range = None

    async def daily_expense_totals(self, start: date, end: date) -> dict[date, int]:
        self.requested_range = (start, end)
        return self.totals


@pytest.mark.asyncio
async def test_expense_trend_compares_current_month_with_previous_twelve() -> None:
    today = date(2026, 3, 15)
    current_start = today.replace(day=1)
    totals = {
        date(2026, 3, 1): 10,
        date(2026, 3, 3): 20,
    }
    for offset in range(-12, 0):
        month = shift_month(current_start, offset)
        totals[month] = 120
    totals[date(2026, 2, 28)] = 120
    repository = DailyTotalsRepository(totals)

    trend = await ExpenseService(repository).build_expense_trend(today)

    assert repository.requested_range == (date(2025, 3, 1), date(2026, 4, 1))
    assert trend.current_cumulative[:3] == [10, 10, 30]
    assert trend.current_cumulative[14] == 30
    assert trend.current_cumulative[15] is None
    assert trend.average_cumulative[0] == 120
    assert trend.average_cumulative[30] == 130


def test_render_expense_trend_returns_png() -> None:
    trend = ExpenseTrend(
        days=[1, 2, 3],
        current_cumulative=[100, 150, 225],
        average_cumulative=[80.0, 140.0, 200.0],
        today_day=3,
        current_month_label="09.2026",
        history_label="09.2025–08.2026",
    )

    image = render_expense_trend(trend)

    assert image.read(8) == b"\x89PNG\r\n\x1a\n"
