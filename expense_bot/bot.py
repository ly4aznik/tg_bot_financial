from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from telegram import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from expense_bot.errors import ExpenseBotError, LLMParseError
from expense_bot.models import ExpenseRecognitionResult
from expense_bot.services.audit_logger import AuditLogger
from expense_bot.services.expense_service import ExpenseService

LOGGER = logging.getLogger(__name__)
PENDING_EXPENSES_KEY = "pending_expenses"
ACTIVE_PENDING_EXPENSE_ID_KEY = "active_pending_expense_id"
CALLBACK_PREFIX = "expense"


class ExpenseTelegramBot:
    def __init__(
        self,
        token: str,
        service: ExpenseService,
        expense_types: list[str],
        audit_logger: AuditLogger,
        test_mode: bool = False,
    ) -> None:
        self._token = token
        self._service = service
        self._expense_types = expense_types
        self._audit_logger = audit_logger
        self._test_mode = test_mode
        self._storage_name = "console" if test_mode else "google_sheets"

    def build_application(self) -> Application:
        application = (
            Application.builder()
            .token(self._token)
            .post_init(self._post_init)
            .build()
        )
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("help", self.start))
        application.add_handler(CommandHandler("categories", self.show_categories))
        application.add_handler(CommandHandler("types", self.show_categories))
        application.add_handler(
            CallbackQueryHandler(self.handle_confirmation, pattern=r"^expense:")
        )
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_expense_message)
        )
        application.add_error_handler(self.handle_error)
        return application

    async def _post_init(self, application: Application) -> None:
        await application.bot.set_my_commands(self._build_bot_commands())

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return

        examples = [
            "650 обед",
            "Вчера самокат 420 тип: Транспорт",
            "05.04 линзы 523 тип: Здоровье/Медицина/Уход",
        ]
        mode_text = (
            "Сейчас включен тестовый режим: после подтверждения я выведу запись в консоль, а не в Google Sheets.\n\n"
            if self._test_mode
            else "После распознавания я покажу запись и попрошу подтвердить ее перед сохранением в Google Sheets.\n\n"
        )
        text = (
            "Я принимаю траты в свободной форме.\n\n"
            + mode_text
            + "Примеры сообщений:\n"
            + "\n".join(f"- {example}" for example in examples)
            + "\n\nДоступные типы трат:\n"
            + self._format_expense_types()
        )
        await update.message.reply_text(text)

    async def show_categories(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not update.message:
            return
        await update.message.reply_text(
            "Доступные типы трат:\n" + self._format_expense_types()
        )

    async def handle_expense_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not update.message or not update.effective_user or not update.effective_chat:
            return

        raw_text = update.message.text.strip()
        if not raw_text:
            await update.message.reply_text("Нужно текстовое сообщение с описанием траты.")
            return

        user_context = self._build_update_context(update)
        self._audit_logger.log_event(
            "user_message_received",
            **user_context,
            raw_text=raw_text,
        )

        active_pending_id = self._get_active_pending_id(context)
        active_pending = self._load_pending_result(context, active_pending_id) if active_pending_id else None

        if active_pending_id and active_pending:
            if self._is_text_confirmation(raw_text):
                await self._handle_text_confirmation(
                    message=update.message,
                    context=context,
                    pending_id=active_pending_id,
                    recognition_result=active_pending,
                    user_context=user_context,
                    raw_text=raw_text,
                )
                return

            if self._is_text_cancellation(raw_text):
                await self._handle_text_cancellation(
                    message=update.message,
                    context=context,
                    pending_id=active_pending_id,
                    recognition_result=active_pending,
                    user_context=user_context,
                    raw_text=raw_text,
                )
                return

            if self._is_correction_instruction(raw_text):
                await self._handle_text_correction(
                    message=update.message,
                    context=context,
                    pending_id=active_pending_id,
                    recognition_result=active_pending,
                    user_context=user_context,
                    instruction=raw_text,
                )
                return

        await self._handle_new_expense_message(
            message=update.message,
            context=context,
            user_context=user_context,
            raw_text=raw_text,
        )

    async def handle_confirmation(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        query = update.callback_query
        if not query or not query.data:
            return

        await query.answer()
        parts = query.data.split(":", maxsplit=2)
        if len(parts) != 3:
            await query.edit_message_text("Не удалось обработать подтверждение.")
            return

        _, action, pending_id = parts
        user_context = self._build_query_context(query)
        recognition_result = self._load_pending_result(context, pending_id)
        if recognition_result is None:
            text = "Эта запись уже обработана или больше не ждет подтверждения."
            await query.edit_message_text(text)
            self._log_bot_message(
                stage="missing_pending",
                text=text,
                user_context=user_context,
                pending_id=pending_id,
            )
            return

        expense = recognition_result.expense
        llm_call = recognition_result.llm_call
        user_context = self._build_query_context(query, expense)

        if action == "cancel":
            self._remove_pending_result(context, pending_id)
            text = "Запись отменена. Если нужно, отправь сообщение заново в исправленном виде."
            self._audit_logger.log_event(
                "user_decision",
                **user_context,
                decision="cancel",
                decision_source="button",
                pending_id=pending_id,
                expense=expense,
                llm_call=llm_call,
            )
            await query.edit_message_text(text)
            self._log_bot_message(
                stage="cancelled",
                text=text,
                user_context=user_context,
                pending_id=pending_id,
                expense=expense,
                llm_call=llm_call,
            )
            return

        if action != "confirm":
            text = "Неизвестное действие подтверждения."
            await query.edit_message_text(text)
            self._log_bot_message(
                stage="unknown_confirmation_action",
                text=text,
                user_context=user_context,
                pending_id=pending_id,
            )
            return

        self._audit_logger.log_event(
            "user_decision",
            **user_context,
            decision="confirm",
            decision_source="button",
            pending_id=pending_id,
            expense=expense,
            llm_call=llm_call,
        )
        saving_text = "Сохраняю запись...\n\n" + self._format_expense(expense)
        await query.edit_message_text(saving_text)
        self._log_bot_message(
            stage="saving",
            text=saving_text,
            user_context=user_context,
            pending_id=pending_id,
            expense=expense,
            llm_call=llm_call,
        )

        success, final_text = await self._persist_pending_result(
            context=context,
            pending_id=pending_id,
            recognition_result=recognition_result,
            user_context=user_context,
        )
        await query.edit_message_text(final_text)
        self._log_bot_message(
            stage="persisted" if success else "persist_error",
            text=final_text,
            user_context=user_context,
            pending_id=pending_id,
            expense=expense,
            llm_call=llm_call,
        )

    async def handle_error(
        self,
        update: object,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        LOGGER.error("Unhandled telegram error", exc_info=context.error)

    async def _handle_new_expense_message(
        self,
        message: Message,
        context: ContextTypes.DEFAULT_TYPE,
        user_context: dict[str, object],
        raw_text: str,
    ) -> None:
        processing_text = "Обрабатываю..."
        processing_message = await message.reply_text(processing_text)
        self._log_bot_message(
            stage="processing",
            text=processing_text,
            user_context=user_context,
            raw_text=raw_text,
        )
        await context.bot.send_chat_action(
            chat_id=message.chat_id,
            action=ChatAction.TYPING,
        )

        try:
            recognition_result = await self._service.parse_expense(
                raw_text=raw_text,
                telegram_user_id=message.from_user.id,
                telegram_username=message.from_user.username,
            )
        except ExpenseBotError as exc:
            LOGGER.warning("Failed to process expense message: %s", exc, exc_info=True)
            llm_call = exc.llm_call if isinstance(exc, LLMParseError) else None
            self._audit_logger.log_event(
                "expense_recognition_failed",
                **user_context,
                raw_text=raw_text,
                error=str(exc),
                llm_call=llm_call,
            )
            error_text = (
                "Не получилось обработать трату.\n"
                f"Причина: {exc}\n\n"
                "Попробуй написать, например: `650 обед` или `05.04 линзы 523 тип: Здоровье/Медицина/Уход`."
            )
            await self._safe_edit_message(processing_message, error_text)
            self._log_bot_message(
                stage="parse_error",
                text=error_text,
                user_context=user_context,
                raw_text=raw_text,
                llm_call=llm_call,
            )
            return
        except Exception as exc:
            LOGGER.exception("Unexpected error while handling message")
            self._audit_logger.log_event(
                "expense_recognition_failed",
                **user_context,
                raw_text=raw_text,
                error=str(exc),
            )
            error_text = "Произошла внутренняя ошибка. Проверь логи приложения и попробуй еще раз."
            await self._safe_edit_message(processing_message, error_text)
            self._log_bot_message(
                stage="parse_error",
                text=error_text,
                user_context=user_context,
                raw_text=raw_text,
            )
            return

        pending_id = uuid4().hex[:16]
        self._save_pending_result(context, pending_id, recognition_result)
        confirmation_text = self._format_pending_confirmation(recognition_result.expense)
        self._audit_logger.log_event(
            "expense_recognized",
            **user_context,
            pending_id=pending_id,
            raw_text=raw_text,
            expense=recognition_result.expense,
            llm_call=recognition_result.llm_call,
        )
        await self._safe_edit_message(
            processing_message,
            confirmation_text,
            reply_markup=self._build_confirmation_markup(pending_id),
        )
        self._log_bot_message(
            stage="confirmation",
            text=confirmation_text,
            user_context=user_context,
            pending_id=pending_id,
            raw_text=raw_text,
            expense=recognition_result.expense,
            llm_call=recognition_result.llm_call,
        )

    async def _handle_text_confirmation(
        self,
        message: Message,
        context: ContextTypes.DEFAULT_TYPE,
        pending_id: str,
        recognition_result: ExpenseRecognitionResult,
        user_context: dict[str, object],
        raw_text: str,
    ) -> None:
        expense = recognition_result.expense
        llm_call = recognition_result.llm_call
        self._audit_logger.log_event(
            "user_decision",
            **user_context,
            decision="confirm",
            decision_source="text",
            pending_id=pending_id,
            raw_text=raw_text,
            expense=expense,
            llm_call=llm_call,
        )
        saving_text = "Сохраняю запись...\n\n" + self._format_expense(expense)
        saving_message = await message.reply_text(saving_text)
        self._log_bot_message(
            stage="saving",
            text=saving_text,
            user_context=user_context,
            pending_id=pending_id,
            expense=expense,
            llm_call=llm_call,
        )
        success, final_text = await self._persist_pending_result(
            context=context,
            pending_id=pending_id,
            recognition_result=recognition_result,
            user_context=user_context,
        )
        await self._safe_edit_message(saving_message, final_text)
        self._log_bot_message(
            stage="persisted" if success else "persist_error",
            text=final_text,
            user_context=user_context,
            pending_id=pending_id,
            expense=expense,
            llm_call=llm_call,
        )

    async def _handle_text_cancellation(
        self,
        message: Message,
        context: ContextTypes.DEFAULT_TYPE,
        pending_id: str,
        recognition_result: ExpenseRecognitionResult,
        user_context: dict[str, object],
        raw_text: str,
    ) -> None:
        self._remove_pending_result(context, pending_id)
        text = "Запись отменена. Если нужно, отправь сообщение заново в исправленном виде."
        self._audit_logger.log_event(
            "user_decision",
            **user_context,
            decision="cancel",
            decision_source="text",
            pending_id=pending_id,
            raw_text=raw_text,
            expense=recognition_result.expense,
            llm_call=recognition_result.llm_call,
        )
        await message.reply_text(text)
        self._log_bot_message(
            stage="cancelled",
            text=text,
            user_context=user_context,
            pending_id=pending_id,
            expense=recognition_result.expense,
            llm_call=recognition_result.llm_call,
        )

    async def _handle_text_correction(
        self,
        message: Message,
        context: ContextTypes.DEFAULT_TYPE,
        pending_id: str,
        recognition_result: ExpenseRecognitionResult,
        user_context: dict[str, object],
        instruction: str,
    ) -> None:
        expense = recognition_result.expense
        self._audit_logger.log_event(
            "expense_correction_requested",
            **user_context,
            pending_id=pending_id,
            instruction=instruction,
            expense=expense,
            llm_call=recognition_result.llm_call,
        )

        processing_text = "Исправляю распознанную трату..."
        processing_message = await message.reply_text(processing_text)
        self._log_bot_message(
            stage="correction_processing",
            text=processing_text,
            user_context=user_context,
            pending_id=pending_id,
            instruction=instruction,
            expense=expense,
            llm_call=recognition_result.llm_call,
        )
        await context.bot.send_chat_action(
            chat_id=message.chat_id,
            action=ChatAction.TYPING,
        )

        try:
            revised_result = await self._service.revise_expense(expense, instruction)
        except ExpenseBotError as exc:
            LOGGER.warning("Failed to revise pending expense: %s", exc, exc_info=True)
            llm_call = exc.llm_call if isinstance(exc, LLMParseError) else None
            self._audit_logger.log_event(
                "expense_correction_failed",
                **user_context,
                pending_id=pending_id,
                instruction=instruction,
                expense=expense,
                llm_call=llm_call,
                error=str(exc),
            )
            error_text = (
                "Не получилось исправить запись.\n"
                f"Причина: {exc}\n\n"
                "Попробуй написать точнее, например: `исправь сумму на 1000`, `поменяй категорию на Транспорт`, `поставь дату 2026-04-06`."
            )
            await self._safe_edit_message(processing_message, error_text)
            self._log_bot_message(
                stage="correction_error",
                text=error_text,
                user_context=user_context,
                pending_id=pending_id,
                instruction=instruction,
                expense=expense,
                llm_call=llm_call,
            )
            return
        except Exception as exc:
            LOGGER.exception("Unexpected error while revising pending expense")
            self._audit_logger.log_event(
                "expense_correction_failed",
                **user_context,
                pending_id=pending_id,
                instruction=instruction,
                expense=expense,
                error=str(exc),
            )
            error_text = "Произошла внутренняя ошибка при исправлении записи."
            await self._safe_edit_message(processing_message, error_text)
            self._log_bot_message(
                stage="correction_error",
                text=error_text,
                user_context=user_context,
                pending_id=pending_id,
                instruction=instruction,
                expense=expense,
            )
            return

        self._save_pending_result(context, pending_id, revised_result)
        confirmation_text = self._format_corrected_confirmation(revised_result.expense)
        self._audit_logger.log_event(
            "expense_corrected",
            **user_context,
            pending_id=pending_id,
            instruction=instruction,
            previous_expense=expense,
            expense=revised_result.expense,
            llm_call=revised_result.llm_call,
        )
        await self._safe_edit_message(
            processing_message,
            confirmation_text,
            reply_markup=self._build_confirmation_markup(pending_id),
        )
        self._log_bot_message(
            stage="correction_confirmation",
            text=confirmation_text,
            user_context=user_context,
            pending_id=pending_id,
            instruction=instruction,
            expense=revised_result.expense,
            llm_call=revised_result.llm_call,
        )

    async def _persist_pending_result(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        pending_id: str,
        recognition_result: ExpenseRecognitionResult,
        user_context: dict[str, object],
    ) -> tuple[bool, str]:
        expense = recognition_result.expense
        llm_call = recognition_result.llm_call
        try:
            await self._service.persist_expense(expense)
        except ExpenseBotError as exc:
            LOGGER.warning("Failed to persist expense: %s", exc, exc_info=True)
            error_text = (
                "Не получилось сохранить запись после подтверждения. Проверь настройки и попробуй еще раз.\n\n"
                f"Причина: {exc}"
            )
            self._audit_logger.log_event(
                "expense_persist_failed",
                **user_context,
                pending_id=pending_id,
                expense=expense,
                llm_call=llm_call,
                storage=self._storage_name,
                error=str(exc),
            )
            return False, error_text
        except Exception as exc:
            LOGGER.exception("Unexpected error while persisting expense")
            error_text = "Произошла внутренняя ошибка при сохранении записи."
            self._audit_logger.log_event(
                "expense_persist_failed",
                **user_context,
                pending_id=pending_id,
                expense=expense,
                llm_call=llm_call,
                storage=self._storage_name,
                error=str(exc),
            )
            return False, error_text

        self._remove_pending_result(context, pending_id)
        saved_text = (
            "Тестовый режим: запись подтверждена и выведена в консоль."
            if self._test_mode
            else "Запись подтверждена и сохранена в Google Sheets."
        )
        final_text = saved_text + "\n\n" + self._format_expense(expense)
        self._audit_logger.log_event(
            "expense_persisted",
            **user_context,
            pending_id=pending_id,
            expense=expense,
            llm_call=llm_call,
            storage=self._storage_name,
        )
        return True, final_text

    def _save_pending_result(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        pending_id: str,
        recognition_result: ExpenseRecognitionResult,
    ) -> None:
        self._get_pending_store(context)[pending_id] = recognition_result.model_dump(mode="json")
        self._set_active_pending_id(context, pending_id)

    def _load_pending_result(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        pending_id: str | None,
    ) -> ExpenseRecognitionResult | None:
        if not pending_id:
            return None

        expense_data = self._get_pending_store(context).get(pending_id)
        if expense_data is None:
            self._clear_active_pending_id(context, pending_id)
            return None

        try:
            return ExpenseRecognitionResult.model_validate(expense_data)
        except ValidationError:
            LOGGER.warning(
                "Pending expense payload is invalid: %s",
                expense_data,
                exc_info=True,
            )
            self._get_pending_store(context).pop(pending_id, None)
            self._clear_active_pending_id(context, pending_id)
            return None

    def _remove_pending_result(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        pending_id: str,
    ) -> None:
        self._get_pending_store(context).pop(pending_id, None)
        self._clear_active_pending_id(context, pending_id)

    def _build_confirmation_markup(self, pending_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="Да, записать",
                        callback_data=f"{CALLBACK_PREFIX}:confirm:{pending_id}",
                    ),
                    InlineKeyboardButton(
                        text="Нет, отменить",
                        callback_data=f"{CALLBACK_PREFIX}:cancel:{pending_id}",
                    ),
                ]
            ]
        )

    def _format_expense_types(self) -> str:
        return "\n".join(f"- {expense_type}" for expense_type in self._expense_types)

    @staticmethod
    def _build_bot_commands() -> list[BotCommand]:
        return [
            BotCommand("start", "Показать справку и примеры"),
            BotCommand("categories", "Показать текущие категории трат"),
            BotCommand("help", "Показать справку"),
        ]

    def _format_pending_confirmation(self, expense: Any) -> str:
        return (
            "Я распознал такую трату:\n\n"
            + self._format_expense(expense)
            + "\n\nПри необходимости можно изменить данные кнопкой или сообщением."
        )

    def _format_corrected_confirmation(self, expense: Any) -> str:
        return (
            "Обновил распознанную трату:\n\n"
            + self._format_expense(expense)
            + "\n\nВсе верно? Можно снова нажать кнопку или написать следующее исправление."
        )

    def _format_expense(self, expense: Any) -> str:
        return (
            f"Тип траты: {expense.expense_type.value}\n"
            f"Дата траты: {expense.expense_date.isoformat()}\n"
            f"Сумма траты: {expense.expense_amount:.2f}\n"
            f"Описание траты: {expense.expense_description}"
        )

    def _get_pending_store(
        self,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> dict[str, dict[str, object]]:
        store = context.user_data.get(PENDING_EXPENSES_KEY)
        if not isinstance(store, dict):
            store = {}
            context.user_data[PENDING_EXPENSES_KEY] = store
        return store

    def _get_active_pending_id(self, context: ContextTypes.DEFAULT_TYPE) -> str | None:
        pending_id = context.user_data.get(ACTIVE_PENDING_EXPENSE_ID_KEY)
        return pending_id if isinstance(pending_id, str) and pending_id else None

    def _set_active_pending_id(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        pending_id: str,
    ) -> None:
        context.user_data[ACTIVE_PENDING_EXPENSE_ID_KEY] = pending_id

    def _clear_active_pending_id(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        pending_id: str | None = None,
    ) -> None:
        active_pending_id = self._get_active_pending_id(context)
        if pending_id is None or active_pending_id == pending_id:
            context.user_data.pop(ACTIVE_PENDING_EXPENSE_ID_KEY, None)

    def _build_update_context(self, update: Update) -> dict[str, object]:
        user = update.effective_user
        chat = update.effective_chat
        message = update.message
        return {
            "telegram_user_id": user.id if user else None,
            "telegram_username": user.username if user else None,
            "telegram_full_name": user.full_name if user else None,
            "chat_id": chat.id if chat else None,
            "message_id": message.message_id if message else None,
        }

    def _build_query_context(self, query: CallbackQuery, expense: Any | None = None) -> dict[str, object]:
        message = query.message
        user = query.from_user
        return {
            "telegram_user_id": user.id if user else getattr(expense, "telegram_user_id", None),
            "telegram_username": user.username if user else getattr(expense, "telegram_username", None),
            "telegram_full_name": user.full_name if user else None,
            "chat_id": message.chat_id if message else None,
            "message_id": message.message_id if message else None,
        }

    def _log_bot_message(
        self,
        stage: str,
        text: str,
        user_context: dict[str, object],
        **extra: Any,
    ) -> None:
        self._audit_logger.log_event(
            "bot_message_sent",
            stage=stage,
            bot_message=text,
            **user_context,
            **extra,
        )

    def _is_text_confirmation(self, text: str) -> bool:
        normalized = self._normalize_follow_up_text(text)
        return normalized in {
            "да",
            "да все верно",
            "все верно",
            "верно",
            "ок",
            "окей",
            "сохрани",
            "сохранить",
            "записать",
            "записывай",
        }

    def _is_text_cancellation(self, text: str) -> bool:
        normalized = self._normalize_follow_up_text(text)
        return normalized in {
            "нет",
            "отмена",
            "отмени",
            "не надо",
            "не записывай",
            "не сохраняй",
        }

    def _is_correction_instruction(self, text: str) -> bool:
        normalized = self._normalize_follow_up_text(text)
        correction_cues = (
            "исправ",
            "поменя",
            "измени",
            "замени",
            "поставь",
            "укажи",
            "сделай",
            "сумм",
            "стоим",
            "категор",
            "тип",
            "дат",
            "описан",
            "назван",
        )
        return normalized.startswith("нет ") or any(cue in normalized for cue in correction_cues)

    @staticmethod
    def _normalize_follow_up_text(text: str) -> str:
        return " ".join(text.strip().lower().replace("ё", "е").split())

    async def _safe_edit_message(
        self,
        message: Message,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        try:
            await message.edit_text(text, reply_markup=reply_markup)
        except BadRequest:
            await message.reply_text(text, reply_markup=reply_markup)

