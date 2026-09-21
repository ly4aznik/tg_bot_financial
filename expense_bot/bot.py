from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from html import escape
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from telegram import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Message, Update
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from expense_bot.categories import ExpenseType
from expense_bot.models import ExpenseRecord, RecentExpense
from expense_bot.services.audit_logger import AuditLogger
from expense_bot.services.expense_service import ExpenseService
from expense_bot.trend_chart import render_expense_trend

LOGGER = logging.getLogger(__name__)
DRAFT_KEY = "expense_draft_v2"
CALLBACK_PREFIX = "e2"
SUMMARY_LABELS = {
    ExpenseType.FOOD_DELIVERY: "Общепит/доставка",
    ExpenseType.HEALTH: "Здоровье/уход",
    ExpenseType.BASIC_GROCERIES: "Продукты",
    ExpenseType.MANDATORY_PAYMENTS: "Платежи/подписки",
    ExpenseType.PETS: "Дом. животные",
}


def parse_manual_date(value: str, year: int) -> date:
    """Parse DD.MM using the explicitly supplied current year."""
    try:
        return datetime.strptime(f"{value.strip()}.{year}", "%d.%m.%Y").date()
    except ValueError as exc:
        raise ValueError("Введи существующую дату в формате ДД.ММ, например 05.09.") from exc


class ExpenseTelegramBot:
    def __init__(
        self,
        token: str,
        service: ExpenseService,
        audit_logger: AuditLogger,
        timezone: ZoneInfo,
    ) -> None:
        self._token = token
        self._service = service
        self._audit_logger = audit_logger
        self._timezone = timezone

    def build_application(self) -> Application:
        application = Application.builder().token(self._token).post_init(self._post_init).build()
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("help", self.start))
        application.add_handler(CommandHandler("add", self.add))
        application.add_handler(CommandHandler("cancel", self.cancel))
        application.add_handler(CommandHandler("summary", self.summary))
        application.add_handler(CommandHandler("recent", self.recent))
        application.add_handler(CommandHandler("trend", self.trend))
        application.add_handler(CommandHandler(["categories", "types"], self.show_categories))
        application.add_handler(CallbackQueryHandler(self.handle_callback, pattern=r"^e2:"))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        application.add_error_handler(self.handle_error)
        return application

    async def _post_init(self, application: Application) -> None:
        await application.bot.set_my_commands([
            BotCommand("add", "Добавить расход"),
            BotCommand("cancel", "Отменить текущий ввод"),
            BotCommand("summary", "Сводка за месяц и год"),
            BotCommand("recent", "Последние 10 расходов"),
            BotCommand("trend", "График расходов и тренда"),
            BotCommand("categories", "Показать категории"),
            BotCommand("help", "Показать справку"),
        ])

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        await update.message.reply_text(
            "Бот учёта расходов 2.0. Данные заполняются пошагово и сохраняются в SQLite.",
            reply_markup=self._start_markup(),
        )

    async def add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        draft = self._new_draft()
        context.user_data[DRAFT_KEY] = draft
        self._audit("expense_started", update, flow_id=draft["flow_id"])
        await update.message.reply_text("Выбери дату траты:", reply_markup=self._date_markup(draft["flow_id"]))

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        draft = self._draft(context)
        context.user_data.pop(DRAFT_KEY, None)
        if update.message:
            await update.message.reply_text(
                "Ввод расхода отменён." if draft else "Сейчас нет незавершённого расхода.",
                reply_markup=self._start_markup(),
            )
        if draft:
            self._audit("expense_cancelled", update, flow_id=draft["flow_id"], source="command")

    async def show_categories(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message:
            await update.message.reply_text(
                "Доступные категории:\n"
                + "\n".join(f"• {expense_type.value}" for expense_type in ExpenseType)
            )

    async def summary(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        today = datetime.now(self._timezone).date()
        month_start = today.replace(day=1)
        next_month_start = (
            date(today.year + 1, 1, 1)
            if today.month == 12
            else date(today.year, today.month + 1, 1)
        )
        year_start = date(today.year, 1, 1)
        next_year_start = date(today.year + 1, 1, 1)
        totals = await self._service.summarize_expenses(
            month_start,
            next_month_start,
            year_start,
            next_year_start,
        )
        await update.message.reply_text(
            self._format_summary(totals, today),
            parse_mode="HTML",
        )
        self._audit("summary_requested", update, month=today.month, year=today.year)

    async def recent(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        expenses = await self._service.list_recent_expenses()
        await update.message.reply_text(self._format_recent_expenses(expenses))
        self._audit("recent_expenses_requested", update, count=len(expenses))

    async def trend(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        await self._send_trend_chart(update.message, update)

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        draft = self._draft(context)
        if not draft:
            await update.message.reply_text("Нажми «Добавить расход» или используй /add.", reply_markup=self._start_markup())
            return
        text = update.message.text.strip()
        step = draft["step"]
        if step == "custom_date":
            await self._accept_custom_date(update.message, draft, text)
        elif step == "amount":
            await self._accept_amount(update.message, draft, text)
        elif step == "description":
            await self._accept_description(update.message, draft, text)
        else:
            await update.message.reply_text("Используй кнопки под текущим сообщением или /cancel.")

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.data:
            return
        await query.answer()
        parts = query.data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        if action == "new":
            draft = self._new_draft()
            context.user_data[DRAFT_KEY] = draft
            self._audit("expense_started", update, flow_id=draft["flow_id"])
            # Keep the previous saved-expense message in chat history. Starting
            # another expense must create a fresh message instead of editing it.
            if query.message:
                await query.message.reply_text(
                    "Выбери дату траты:",
                    reply_markup=self._date_markup(draft["flow_id"]),
                )
            return
        if action == "recent":
            expenses = await self._service.list_recent_expenses()
            if query.message:
                await query.message.reply_text(self._format_recent_expenses(expenses))
            self._audit("recent_expenses_requested", update, count=len(expenses))
            return
        if action == "trend":
            if query.message:
                await self._send_trend_chart(query.message, update)
            return

        draft = self._draft(context)
        flow_id = parts[2] if len(parts) > 2 else ""
        if not draft or flow_id != draft["flow_id"]:
            await self._edit(query, "Этот сценарий уже завершён или устарел. Начни новый расход.", self._start_markup())
            return

        if action == "date" and len(parts) == 4:
            await self._choose_date(query, draft, parts[3])
        elif action == "cat" and len(parts) == 4:
            await self._choose_category(query, draft, parts[3])
        elif action == "edit" and len(parts) == 4:
            await self._edit_field(query, draft, parts[3])
        elif action == "save":
            await self._save(query, context, draft, update)
        elif action == "cancel":
            context.user_data.pop(DRAFT_KEY, None)
            self._audit("expense_cancelled", update, flow_id=flow_id, source="button")
            await self._edit(query, "Ввод расхода отменён.", self._start_markup())
        else:
            await self._edit(query, "Неизвестное или устаревшее действие.", self._start_markup())

    async def _choose_date(self, query: CallbackQuery, draft: dict[str, Any], choice: str) -> None:
        today = datetime.now(self._timezone).date()
        if choice == "today":
            draft["expense_date"] = today.isoformat()
        elif choice == "yesterday":
            draft["expense_date"] = (today - timedelta(days=1)).isoformat()
        elif choice == "custom":
            draft["step"] = "custom_date"
            await self._edit(query, "Введи дату в формате ДД.ММ. Год будет текущим.")
            return
        else:
            return
        self._log_step(query, draft, "date_selected", expense_date=draft["expense_date"])
        await self._after_field(query, draft, "amount", "Введи целую сумму траты, например 650 или 1250:")

    async def _accept_custom_date(self, message: Message, draft: dict[str, Any], text: str) -> None:
        try:
            parsed = parse_manual_date(text, datetime.now(self._timezone).year)
        except ValueError as exc:
            await message.reply_text(str(exc))
            return
        draft["expense_date"] = parsed.isoformat()
        self._log_step(message, draft, "date_selected", expense_date=draft["expense_date"])
        await self._after_text_field(message, draft, "amount", "Введи целую сумму траты, например 650 или 1250:")

    async def _accept_amount(self, message: Message, draft: dict[str, Any], text: str) -> None:
        try:
            amount = ExpenseRecord.parse_expense_amount(text)
            amount = ExpenseRecord.validate_expense_amount(amount)
        except (TypeError, ValueError) as exc:
            await message.reply_text(str(exc))
            return
        draft["expense_amount"] = str(amount)
        self._log_step(message, draft, "amount_entered", expense_amount=str(amount))
        if draft.pop("editing", False):
            draft["step"] = "summary"
            await message.reply_text(self._summary(draft), reply_markup=self._summary_markup(draft["flow_id"]))
        else:
            draft["step"] = "description"
            await message.reply_text("Введи короткое описание траты:")

    async def _choose_category(self, query: CallbackQuery, draft: dict[str, Any], code: str) -> None:
        try:
            category = ExpenseType[code]
        except KeyError:
            await self._edit(query, "Категория не найдена. Выбери категорию заново.", self._category_markup(draft["flow_id"]))
            return
        draft["expense_type"] = category.value
        self._log_step(query, draft, "category_selected", expense_type=category.value)
        draft.pop("editing", None)
        draft["step"] = "summary"
        await self._edit(query, self._summary(draft), self._summary_markup(draft["flow_id"]))

    async def _accept_description(self, message: Message, draft: dict[str, Any], text: str) -> None:
        try:
            description = ExpenseRecord.validate_expense_description(text)
        except ValueError as exc:
            await message.reply_text(str(exc))
            return
        draft["expense_description"] = description
        self._log_step(message, draft, "description_entered")
        if draft.pop("editing", False):
            draft["step"] = "summary"
            await message.reply_text(self._summary(draft), reply_markup=self._summary_markup(draft["flow_id"]))
        else:
            draft["step"] = "category"
            await message.reply_text("Выбери категорию:", reply_markup=self._category_markup(draft["flow_id"]))

    async def _edit_field(self, query: CallbackQuery, draft: dict[str, Any], field: str) -> None:
        draft["editing"] = True
        if field == "date":
            draft["step"] = "date"
            await self._edit(query, "Выбери новую дату:", self._date_markup(draft["flow_id"]))
        elif field == "amount":
            draft["step"] = "amount"
            await self._edit(query, "Введи новую сумму:")
        elif field == "category":
            draft["step"] = "category"
            await self._edit(query, "Выбери новую категорию:", self._category_markup(draft["flow_id"]))
        elif field == "description":
            draft["step"] = "description"
            await self._edit(query, "Введи новое описание:")

    async def _after_field(self, query: CallbackQuery, draft: dict[str, Any], next_step: str, prompt: str) -> None:
        if draft.pop("editing", False):
            draft["step"] = "summary"
            await self._edit(query, self._summary(draft), self._summary_markup(draft["flow_id"]))
        else:
            draft["step"] = next_step
            markup = self._category_markup(draft["flow_id"]) if next_step == "category" else None
            await self._edit(query, prompt, markup)

    async def _after_text_field(self, message: Message, draft: dict[str, Any], next_step: str, prompt: str) -> None:
        if draft.pop("editing", False):
            draft["step"] = "summary"
            await message.reply_text(self._summary(draft), reply_markup=self._summary_markup(draft["flow_id"]))
        else:
            draft["step"] = next_step
            await message.reply_text(prompt)

    async def _save(self, query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, draft: dict[str, Any], update: Update) -> None:
        if draft.get("step") != "summary" or draft.get("saving"):
            await self._edit(query, "Эта запись уже обрабатывается или данные ещё не заполнены.")
            return
        draft["saving"] = True
        try:
            record = ExpenseRecord.model_validate({
                **draft,
                "telegram_user_id": query.from_user.id,
                "telegram_username": query.from_user.username,
            })
            await self._service.persist_expense(record)
        except Exception as exc:
            draft["saving"] = False
            LOGGER.exception("Failed to persist development expense")
            self._audit("expense_persist_failed", update, flow_id=draft["flow_id"], error=str(exc))
            await self._edit(query, "Не удалось обработать запись. Попробуй сохранить ещё раз.", self._summary_markup(draft["flow_id"]))
            return
        context.user_data.pop(DRAFT_KEY, None)
        self._audit("expense_persisted", update, flow_id=draft["flow_id"], storage="sqlite", expense=record)
        await self._edit(query, "Расход сохранён.\n\n" + self._format_expense(record), self._start_markup())

    def _new_draft(self) -> dict[str, Any]:
        return {"flow_id": uuid4().hex[:12], "step": "date"}

    @staticmethod
    def _draft(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any] | None:
        value = context.user_data.get(DRAFT_KEY)
        return value if isinstance(value, dict) else None

    @staticmethod
    def _start_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Добавить расход", callback_data=f"{CALLBACK_PREFIX}:new")],
            [InlineKeyboardButton("🕘 Последние 10", callback_data=f"{CALLBACK_PREFIX}:recent")],
            [InlineKeyboardButton("📈 Расходы и тренд", callback_data=f"{CALLBACK_PREFIX}:trend")],
        ])

    @staticmethod
    def _date_markup(flow_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("Сегодня", callback_data=f"e2:date:{flow_id}:today"),
            InlineKeyboardButton("Вчера", callback_data=f"e2:date:{flow_id}:yesterday"),
        ], [InlineKeyboardButton("Другая дата", callback_data=f"e2:date:{flow_id}:custom")]])

    @staticmethod
    def _category_markup(flow_id: str) -> InlineKeyboardMarkup:
        buttons = [InlineKeyboardButton(item.value, callback_data=f"e2:cat:{flow_id}:{item.name}") for item in ExpenseType]
        return InlineKeyboardMarkup([buttons[index:index + 2] for index in range(0, len(buttons), 2)])

    @staticmethod
    def _summary_markup(flow_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Сохранить", callback_data=f"e2:save:{flow_id}"), InlineKeyboardButton("❌ Отмена", callback_data=f"e2:cancel:{flow_id}")],
            [InlineKeyboardButton("Изменить дату", callback_data=f"e2:edit:{flow_id}:date"), InlineKeyboardButton("Изменить сумму", callback_data=f"e2:edit:{flow_id}:amount")],
            [InlineKeyboardButton("Изменить категорию", callback_data=f"e2:edit:{flow_id}:category")],
            [InlineKeyboardButton("Изменить описание", callback_data=f"e2:edit:{flow_id}:description")],
        ])

    def _summary(self, draft: dict[str, Any]) -> str:
        return "Проверь расход:\n\n" + self._format_expense(draft) + "\n\nСохранить или изменить данные?"

    @staticmethod
    def _format_summary(totals: dict[str, tuple[int, int]], today: date) -> str:
        month_labels = [
            "",
            "Январь",
            "Февраль",
            "Март",
            "Апрель",
            "Май",
            "Июнь",
            "Июль",
            "Август",
            "Сентябрь",
            "Октябрь",
            "Ноябрь",
            "Декабрь",
        ]
        month_total = sum(value[0] for value in totals.values())
        year_total = sum(value[1] for value in totals.values())
        month_table = ExpenseTelegramBot._format_period_table(totals, 0, month_total)
        year_table = ExpenseTelegramBot._format_period_table(totals, 1, year_total)
        empty_categories = [
            expense_type.value
            for expense_type in ExpenseType
            if totals.get(expense_type.value, (0, 0)) == (0, 0)
        ]
        empty_text = ", ".join(empty_categories) if empty_categories else "нет"
        return (
            f"<b>{month_labels[today.month]} {today.year}</b>\n"
            f"<pre>{escape(month_table)}</pre>\n"
            f"<b>{today.year} год</b>\n"
            f"<pre>{escape(year_table)}</pre>\n"
            f"<b>Без расходов в {today.year} году:</b> {escape(empty_text)}"
        )

    @staticmethod
    def _format_period_table(
        totals: dict[str, tuple[int, int]],
        period_index: int,
        grand_total: int,
    ) -> str:
        rows = []
        for expense_type in ExpenseType:
            amount = totals.get(expense_type.value, (0, 0))[period_index]
            if amount:
                label = SUMMARY_LABELS.get(expense_type, expense_type.value)
                rows.append(f"{label:<18} {amount:>9,}")
        divider = "-" * 28
        table = [
            f"{'Категория':<18} {'Сумма':>9}",
            divider,
            *rows,
            divider,
            f"{'ИТОГО':<18} {grand_total:>9,}",
        ]
        return "\n".join(table).replace(",", " ")

    @staticmethod
    def _format_expense(expense: Any) -> str:
        def get(name: str) -> Any:
            return expense.get(name) if isinstance(expense, dict) else getattr(expense, name)
        return (
            f"Дата: {get('expense_date')}\n"
            f"Сумма: {Decimal(str(get('expense_amount'))):.0f}\n"
            f"Категория: {get('expense_type').value if isinstance(get('expense_type'), ExpenseType) else get('expense_type')}\n"
            f"Описание: {get('expense_description')}"
        )

    @staticmethod
    def _format_recent_expenses(expenses: list[RecentExpense]) -> str:
        if not expenses:
            return "Сохранённых расходов пока нет."
        blocks = ["Последние 10 расходов:"]
        for index, expense in zip(range(len(expenses), 0, -1), expenses):
            blocks.append(
                f"{index}. {expense.expense_date:%d.%m.%Y} — {expense.expense_amount:,}".replace(",", " ")
                + f"\n{expense.expense_type} · {expense.expense_description or 'без описания'}"
            )
        return "\n\n".join(blocks)

    async def _send_trend_chart(self, message: Message, update: Update) -> None:
        today = datetime.now(self._timezone).date()
        trend = await self._service.build_expense_trend(today)
        image = await asyncio.to_thread(render_expense_trend, trend)
        current_total = next(
            value for value in reversed(trend.current_cumulative) if value is not None
        )
        average_today = trend.average_cumulative[today.day - 1]
        difference = current_total - average_today
        sign = "+" if difference > 0 else ""
        caption = (
            f"На {today:%d.%m.%Y}: {current_total:,.0f}\n"
            f"Среднее на этот день: {average_today:,.0f}\n"
            f"Отклонение: {sign}{difference:,.0f}"
        ).replace(",", " ")
        await message.reply_document(
            document=InputFile(image, filename=f"expense-trend-{today:%Y-%m}.png"),
            caption=caption,
        )
        self._audit("expense_trend_requested", update, month=today.month, year=today.year)

    async def _edit(self, query: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
        try:
            await query.edit_message_text(text, reply_markup=markup)
        except BadRequest:
            if query.message:
                await query.message.reply_text(text, reply_markup=markup)

    def _audit(self, event: str, update: Update, **payload: Any) -> None:
        self._audit_logger.log_event(event, telegram_user_id=update.effective_user.id if update.effective_user else None, chat_id=update.effective_chat.id if update.effective_chat else None, **payload)

    def _log_step(self, source: Message | CallbackQuery, draft: dict[str, Any], event: str, **payload: Any) -> None:
        user = source.from_user
        self._audit_logger.log_event(event, telegram_user_id=user.id if user else None, flow_id=draft["flow_id"], **payload)

    async def handle_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        LOGGER.error("Unhandled telegram error", exc_info=context.error)
