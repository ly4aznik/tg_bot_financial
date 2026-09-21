from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from telegram.ext import ApplicationHandlerStop

from expense_bot.bot import ExpenseTelegramBot
from expense_bot.services.audit_logger import AuditLogger
from expense_bot.services.expense_service import ExpenseService


class EmptyRepository:
    pass


def build_bot(tmp_path: Path) -> ExpenseTelegramBot:
    return ExpenseTelegramBot(
        token="test-token",
        service=ExpenseService(EmptyRepository()),
        audit_logger=AuditLogger(tmp_path / "audit.jsonl"),
        timezone=ZoneInfo("Europe/Moscow"),
        allowed_user_ids=frozenset({42}),
    )


@pytest.mark.asyncio
async def test_allowed_user_passes_access_control(tmp_path: Path) -> None:
    update = SimpleNamespace(effective_user=SimpleNamespace(id=42))

    await build_bot(tmp_path)._authorize_update(update, SimpleNamespace())


@pytest.mark.asyncio
async def test_unauthorized_message_is_rejected_and_audited(tmp_path: Path) -> None:
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=99),
        effective_chat=SimpleNamespace(id=100),
        callback_query=None,
        message=message,
    )
    bot = build_bot(tmp_path)

    with pytest.raises(ApplicationHandlerStop):
        await bot._authorize_update(update, SimpleNamespace())

    message.reply_text.assert_awaited_once_with("Доступ к боту запрещён.")
    assert '"event": "access_denied"' in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_unauthorized_callback_is_stopped_with_alert(tmp_path: Path) -> None:
    query = SimpleNamespace(answer=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=99),
        effective_chat=SimpleNamespace(id=100),
        callback_query=query,
        message=None,
    )

    with pytest.raises(ApplicationHandlerStop):
        await build_bot(tmp_path)._authorize_update(update, SimpleNamespace())

    query.answer.assert_awaited_once_with("Доступ запрещён.", show_alert=True)
