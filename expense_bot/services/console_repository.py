from __future__ import annotations

import json
import logging

from expense_bot.models import ExpenseRecord

LOGGER = logging.getLogger(__name__)


class ConsoleExpenseRepository:
    async def append_expense(self, expense: ExpenseRecord) -> None:
        payload = expense.model_dump(mode="json")
        message = json.dumps(payload, ensure_ascii=False)
        LOGGER.info("[TEST MODE] Распознана и подтверждена запись: %s", message)
        print(f"[TEST MODE] Распознана и подтверждена запись: {message}")
