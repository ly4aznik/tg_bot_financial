from __future__ import annotations

from io import BytesIO

import matplotlib

matplotlib.use("Agg")

from matplotlib import pyplot as plt
from matplotlib.ticker import FuncFormatter

from expense_bot.models import ExpenseTrend


def render_expense_trend(trend: ExpenseTrend) -> BytesIO:
    figure, axis = plt.subplots(figsize=(10, 6), dpi=150)
    axis.plot(
        trend.days,
        trend.average_cumulative,
        color="#f59e0b",
        linewidth=2.5,
        linestyle="--",
        label=f"Среднее за 12 месяцев ({trend.history_label})",
    )
    axis.plot(
        trend.days,
        trend.current_cumulative,
        color="#2563eb",
        linewidth=3,
        marker="o",
        markersize=3,
        label=f"Текущий месяц ({trend.current_month_label})",
    )
    axis.axvline(trend.today_day, color="#94a3b8", linewidth=1, alpha=0.7)
    axis.set_title("Накопительные расходы по дням месяца", fontsize=15, pad=14)
    axis.set_xlabel("День месяца")
    axis.set_ylabel("Накопительная сумма")
    axis.set_xlim(1, trend.days[-1])
    axis.set_xticks([day for day in trend.days if day == 1 or day % 5 == 0 or day == trend.days[-1]])
    axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}".replace(",", " ")))
    axis.grid(True, alpha=0.2)
    axis.legend(loc="upper left", frameon=False)
    figure.tight_layout()

    image = BytesIO()
    figure.savefig(image, format="png", bbox_inches="tight")
    plt.close(figure)
    image.seek(0)
    return image
