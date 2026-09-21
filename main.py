import logging

from telegram import Update

from expense_bot.bot import ExpenseTelegramBot
from expense_bot.config import Settings
from expense_bot.services.audit_logger import AuditLogger
from expense_bot.services.expense_service import ExpenseService
from expense_bot.services.sqlite_repository import SQLiteExpenseRepository


def configure_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        level=logging.INFO,
    )
    # python-telegram-bot uses httpx; INFO messages contain full request URLs,
    # including the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    configure_logging()
    settings = Settings()
    audit_logger = AuditLogger(settings.audit_log_path)

    logging.getLogger(__name__).info(
        "Audit log path: %s",
        audit_logger.log_path,
    )

    repository = SQLiteExpenseRepository(settings.sqlite_database_path)
    logging.getLogger(__name__).info(
        "SQLite database path: %s",
        repository.database_path,
    )
    service = ExpenseService(repository=repository)
    bot = ExpenseTelegramBot(
        token=settings.telegram_bot_token,
        service=service,
        audit_logger=audit_logger,
        timezone=settings.tzinfo,
        allowed_user_ids=settings.allowed_user_ids,
    )

    application = bot.build_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
