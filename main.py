import logging

from telegram import Update

from expense_bot.bot import ExpenseTelegramBot
from expense_bot.categories import EXPENSE_TYPE_VALUES
from expense_bot.config import Settings
from expense_bot.services.audit_logger import AuditLogger
from expense_bot.services.console_repository import ConsoleExpenseRepository
from expense_bot.services.expense_service import ExpenseService
from expense_bot.services.google_sheets import GoogleSheetsExpenseRepository
from expense_bot.services.llm_parser import OpenAICompatibleExpenseParser


def configure_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        level=logging.INFO,
    )


def main() -> None:
    configure_logging()
    settings = Settings()
    audit_logger = AuditLogger(settings.audit_log_path)

    logging.getLogger(__name__).info(
        "Audit log path: %s",
        audit_logger.log_path,
    )

    parser = OpenAICompatibleExpenseParser(
        base_url=settings.openai_compatible_base_url,
        model=settings.openai_compatible_model,
        api_key=settings.openai_compatible_api_key,
        timezone=settings.tzinfo,
        timeout_seconds=settings.request_timeout_seconds,
        reasoning_effort=settings.openai_compatible_reasoning_effort,
    )

    if settings.test_mode:
        repository = ConsoleExpenseRepository()
    else:
        repository = GoogleSheetsExpenseRepository(
            service_account_json=settings.google_service_account_json,
            spreadsheet_id=settings.google_spreadsheet_id,
            worksheet_name=settings.google_worksheet_name,
            timeout_seconds=settings.google_api_timeout_seconds,
        )

    service = ExpenseService(
        parser=parser,
        repository=repository,
    )
    bot = ExpenseTelegramBot(
        token=settings.telegram_bot_token,
        service=service,
        expense_types=EXPENSE_TYPE_VALUES,
        audit_logger=audit_logger,
        test_mode=settings.test_mode,
    )

    application = bot.build_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
