from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from expense_bot.models import LLMCallMetrics


class ExpenseBotError(Exception):
    """Base domain error for the expense bot."""


class LLMParseError(ExpenseBotError):
    """Raised when the local LLM cannot parse the expense message."""

    def __init__(
        self,
        message: str,
        llm_call: LLMCallMetrics | None = None,
    ) -> None:
        super().__init__(message)
        self.llm_call = llm_call


class ExpenseValidationError(ExpenseBotError):
    """Raised when parsed expense data does not pass validation."""


class GoogleSheetsError(ExpenseBotError):
    """Raised when the bot cannot write data to Google Sheets."""
