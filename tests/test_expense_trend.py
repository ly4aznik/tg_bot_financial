from datetime import date
from io import BytesIO

import pytest
from PIL import Image

from expense_bot.models import ExpenseTrend
from expense_bot.services.expense_service import ExpenseService, shift_month
from expense_bot.trend_chart import render_expense_trend, render_weekly_year_trend


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

    assert image[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(BytesIO(image)) as rendered:
        assert rendered.mode == "RGB"


@pytest.mark.asyncio
async def test_weekly_year_trend_uses_52_complete_monday_sunday_weeks() -> None:
    repository = DailyTotalsRepository({
        date(2025, 9, 22): 10,
        date(2026, 9, 20): 20,
        date(2026, 9, 21): 30,
        date(2026, 9, 23): 5,
        date(2026, 9, 27): 40,
        date(2026, 9, 28): 999,
    })
    trend = await ExpenseService(repository).build_weekly_year_trend(date(2026, 9, 28))
    assert repository.requested_range == (date(2025, 9, 22), date(2026, 9, 28))
    assert trend.week_start == date(2026, 9, 21)
    assert trend.current_cumulative == [30, 30, 35, 35, 35, 35, 75]
    assert trend.average_cumulative[0] == 10 / 52
    assert trend.average_cumulative[-1] == 30 / 52
    image = render_weekly_year_trend(trend)
    assert image[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_current_week_trend_stops_at_today_and_excludes_future_expenses() -> None:
    repository = DailyTotalsRepository({
        date(2025, 9, 22): 52,
        date(2026, 9, 21): 20,
        date(2026, 9, 23): 30,
        date(2026, 9, 24): 999,
    })
    trend = await ExpenseService(repository).build_weekly_trend(date(2026, 9, 23), current=True)
    assert repository.requested_range == (date(2025, 9, 22), date(2026, 9, 24))
    assert trend.week_start == date(2026, 9, 21)
    assert trend.current_cumulative == [20, 20, 50, None, None, None, None]
    assert trend.average_cumulative[0] == 1
    assert trend.average_cumulative[2] == 1
    assert trend.is_current_week
