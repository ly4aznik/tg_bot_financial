from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from expense_bot.bot import ExpenseTelegramBot
from expense_bot.categories import ExpenseType
from expense_bot.services.audit_logger import AuditLogger
from expense_bot.services.expense_service import ExpenseService


class RecordingRepository:
    def __init__(self) -> None:
        self.records = []

    async def append_expense(self, expense) -> None:
        self.records.append(expense)


def build_bot(tmp_path: Path) -> ExpenseTelegramBot:
    return ExpenseTelegramBot(
        token="test-token",
        service=ExpenseService(RecordingRepository()),
        audit_logger=AuditLogger(tmp_path / "audit.jsonl"),
        timezone=ZoneInfo("Europe/Moscow"),
    )


def test_category_keyboard_contains_all_categories_and_safe_callbacks(tmp_path: Path) -> None:
    markup = build_bot(tmp_path)._category_markup("a1b2c3d4e5f6")
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert len(buttons) == len(ExpenseType) == 20
    assert {button.text for button in buttons} == {item.value for item in ExpenseType}
    assert all(button.callback_data and len(button.callback_data.encode("utf-8")) <= 64 for button in buttons)


def test_summary_keyboard_can_edit_every_field(tmp_path: Path) -> None:
    markup = build_bot(tmp_path)._summary_markup("a1b2c3d4e5f6")
    callbacks = {button.callback_data for row in markup.inline_keyboard for button in row}
    for field in ("date", "amount", "category", "description"):
        assert f"e2:edit:a1b2c3d4e5f6:{field}" in callbacks
    assert "e2:save:a1b2c3d4e5f6" in callbacks
    assert "e2:cancel:a1b2c3d4e5f6" in callbacks


def test_main_uses_sqlite_repository() -> None:
    source = Path("main.py").read_text(encoding="utf-8")
    assert "SQLiteExpenseRepository(settings.sqlite_database_path)" in source


def test_unwritable_audit_log_does_not_break_bot(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path)  # A directory cannot be opened as a JSONL file.
    logger.log_event("test_event", value=1)


def fake_message(text: str = ""):
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=42, username="tester"),
        reply_text=AsyncMock(),
    )


def fake_query(data: str = ""):
    message = fake_message()
    return SimpleNamespace(
        data=data,
        from_user=message.from_user,
        message=message,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_complete_manual_workflow(tmp_path: Path) -> None:
    bot = build_bot(tmp_path)
    context = SimpleNamespace(user_data={})
    draft = bot._new_draft()
    context.user_data["expense_draft_v2"] = draft
    message = fake_message()

    draft["step"] = "custom_date"
    await bot._accept_custom_date(message, draft, "31.12")
    assert draft["step"] == "amount"
    assert draft["expense_date"].endswith("-12-31")

    await bot._accept_amount(message, draft, "1250")
    assert draft["step"] == "category"
    query = fake_query()
    await bot._choose_category(query, draft, "TRANSPORT")
    assert draft["step"] == "description"
    await bot._accept_description(message, draft, "Такси домой")
    assert draft["step"] == "summary"
    assert draft["expense_type"] == "Транспорт"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "expected_step"),
    [("date", "date"), ("amount", "amount"), ("category", "category"), ("description", "description")],
)
async def test_direct_field_editing(tmp_path: Path, field: str, expected_step: str) -> None:
    bot = build_bot(tmp_path)
    draft = {"flow_id": "flow123", "step": "summary"}
    await bot._edit_field(fake_query(), draft, field)
    assert draft["step"] == expected_step
    assert draft["editing"] is True


@pytest.mark.asyncio
async def test_save_is_idempotent_after_draft_is_removed(tmp_path: Path) -> None:
    repository = RecordingRepository()
    bot = ExpenseTelegramBot(
        token="test-token",
        service=ExpenseService(repository),
        audit_logger=AuditLogger(tmp_path / "audit.jsonl"),
        timezone=ZoneInfo("Europe/Moscow"),
    )
    draft = {
        "flow_id": "flow123",
        "step": "summary",
        "expense_date": "2026-09-04",
        "expense_amount": "650",
        "expense_type": "Транспорт",
        "expense_description": "Такси",
    }
    context = SimpleNamespace(user_data={"expense_draft_v2": draft})
    query = fake_query("e2:save:flow123")
    update = SimpleNamespace(
        callback_query=query,
        effective_user=query.from_user,
        effective_chat=SimpleNamespace(id=100),
    )

    await bot.handle_callback(update, context)
    await bot.handle_callback(update, context)

    assert len(repository.records) == 1
    assert "expense_draft_v2" not in context.user_data


@pytest.mark.asyncio
async def test_new_expense_keeps_previous_message(tmp_path: Path) -> None:
    bot = build_bot(tmp_path)
    context = SimpleNamespace(user_data={})
    query = fake_query("e2:new")
    update = SimpleNamespace(
        callback_query=query,
        effective_user=query.from_user,
        effective_chat=SimpleNamespace(id=100),
    )

    await bot.handle_callback(update, context)

    query.edit_message_text.assert_not_awaited()
    query.message.reply_text.assert_awaited_once()
    assert context.user_data["expense_draft_v2"]["step"] == "date"
