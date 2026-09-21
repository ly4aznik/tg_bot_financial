from __future__ import annotations

from io import BytesIO

import matplotlib
from PIL import Image

matplotlib.use("Agg")

from matplotlib import pyplot as plt
from matplotlib.ticker import FuncFormatter

from expense_bot.models import ExpenseTrend


def render_expense_trend(trend: ExpenseTrend) -> bytes:
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

    rendered = BytesIO()
    figure.savefig(rendered, format="png", bbox_inches="tight", facecolor="white")
    plt.close(figure)
    rendered.seek(0)

    # Flatten the Matplotlib RGBA output to a regular RGB PNG. Telegram's
    # photo pipeline can mishandle transparency, while an RGB PNG sent as a
    # document is preserved byte-for-byte.
    result = BytesIO()
    with Image.open(rendered) as image:
        image.convert("RGB").save(result, format="PNG")
    return result.getvalue()
