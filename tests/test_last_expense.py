import asyncio
import sqlite3
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from expense_bot.bot import DELETE_KEY, DRAFT_KEY, ExpenseTelegramBot
from expense_bot.categories import ExpenseType
from expense_bot.models import ExpenseRecord
from expense_bot.services.audit_logger import AuditLogger
from expense_bot.services.expense_service import ExpenseService
from expense_bot.services.sqlite_repository import SQLiteExpenseRepository


def record(amount=650, day=14):
    return ExpenseRecord(
        expense_date=date(2026, 9, day), expense_amount=amount,
        expense_type=ExpenseType.TRANSPORT, expense_description="такси домой",
        telegram_user_id=77, telegram_username="original-author",
    )


@pytest.fixture
def setup_bot(tmp_path):
    repository = SQLiteExpenseRepository(tmp_path / "expenses.sqlite3")
    bot = ExpenseTelegramBot(
        token="test-token", service=ExpenseService(repository),
        audit_logger=AuditLogger(tmp_path / "audit.jsonl"),
        timezone=ZoneInfo("Europe/Moscow"), allowed_user_ids=frozenset({42}),
    )
    user = SimpleNamespace(id=42, username="editor")
    message = SimpleNamespace(reply_text=AsyncMock(), from_user=user)
    update = SimpleNamespace(
        message=message, callback_query=None, effective_user=user,
        effective_chat=SimpleNamespace(id=100),
    )
    context = SimpleNamespace(user_data={})
    return bot, repository, update, context


async def click(bot, update, context, data):
    query = SimpleNamespace(
        data=data, from_user=update.effective_user, message=update.message,
        answer=AsyncMock(), edit_message_text=AsyncMock(),
    )
    update.callback_query = query
    await bot.handle_callback(update, context)
    return query


@pytest.mark.asyncio
async def test_delete_requires_confirmation_and_double_click_keeps_previous_record(setup_bot):
    bot, repository, update, context = setup_bot
    await repository.append_expense(record(100))
    await repository.append_expense(record(200, day=1))
    await bot.delete_last(update, context)
    assert len(await repository.list_recent_expenses(10)) == 2
    token = context.user_data[DELETE_KEY]["token"]
    assert context.user_data[DELETE_KEY]["expense"].expense_amount == 200
    query = await click(bot, update, context, f"e2:delete_confirm:{token}")
    assert "Расход удалён" in query.edit_message_text.call_args.args[0]
    await click(bot, update, context, f"e2:delete_confirm:{token}")
    assert [item.expense_amount for item in await repository.list_recent_expenses(10)] == [100]
    assert DELETE_KEY not in context.user_data
    assert '"event": "expense_deleted"' in bot._audit_logger.log_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_command", [False, True])
async def test_cancel_does_not_delete(setup_bot, cancel_command):
    bot, repository, update, context = setup_bot
    await repository.append_expense(record())
    await click(bot, update, context, "e2:delete_last")
    token = context.user_data[DELETE_KEY]["token"]
    if cancel_command:
        await bot.cancel(update, context)
    else:
        await click(bot, update, context, f"e2:delete_cancel:{token}")
    await click(bot, update, context, f"e2:delete_confirm:{token}")
    assert await repository.get_last_expense() is not None


@pytest.mark.asyncio
async def test_empty_database_commands_create_no_pending_actions(setup_bot):
    bot, _, update, context = setup_bot
    await bot.delete_last(update, context)
    await bot.edit_last(update, context)
    assert not context.user_data
    assert update.message.reply_text.await_count == 2
    assert "пока нет" in update.message.reply_text.call_args.args[0]


@pytest.mark.asyncio
async def test_stale_delete_does_not_remove_new_last_record(setup_bot):
    bot, repository, update, context = setup_bot
    await repository.append_expense(record())
    await bot.delete_last(update, context)
    token = context.user_data[DELETE_KEY]["token"]
    await repository.append_expense(record(900))
    query = await click(bot, update, context, f"e2:delete_confirm:{token}")
    assert "Ничего не удалено" in query.edit_message_text.call_args.args[0]
    assert len(await repository.list_recent_expenses(10)) == 2


@pytest.mark.asyncio
async def test_other_user_or_chat_cannot_confirm_delete(setup_bot):
    bot, repository, update, context = setup_bot
    await repository.append_expense(record())
    await bot.delete_last(update, context)
    token = context.user_data[DELETE_KEY]["token"]
    other_context = SimpleNamespace(user_data={})
    await click(bot, update, other_context, f"e2:delete_confirm:{token}")
    update.effective_chat.id = 200
    await click(bot, update, context, f"e2:delete_confirm:{token}")
    assert await repository.get_last_expense() is not None


@pytest.mark.asyncio
async def test_edit_updates_existing_record_and_preserves_metadata(setup_bot):
    bot, repository, update, context = setup_bot
    await repository.append_expense(record())
    original = await repository.get_last_expense()
    with sqlite3.connect(repository.database_path) as connection:
        metadata = connection.execute("SELECT telegram_user_id, telegram_username, created_at FROM expenses").fetchone()
    await click(bot, update, context, "e2:edit_last")
    draft = context.user_data[DRAFT_KEY]
    flow_id = draft["flow_id"]
    await click(bot, update, context, f"e2:edit:{flow_id}:amount")
    await bot._accept_amount(update.message, draft, "1250")
    await click(bot, update, context, f"e2:edit:{flow_id}:description")
    await bot._accept_description(update.message, draft, "Обед с друзьями")
    await click(bot, update, context, f"e2:edit:{flow_id}:category")
    await click(bot, update, context, f"e2:cat:{flow_id}:LUNCH")
    await click(bot, update, context, f"e2:edit:{flow_id}:date")
    await click(bot, update, context, f"e2:date:{flow_id}:custom")
    await bot._accept_custom_date(update.message, draft, "05.09")
    query = await click(bot, update, context, f"e2:save:{flow_id}")
    assert "Расход изменён" in query.edit_message_text.call_args.args[0]
    updated = await repository.get_last_expense()
    assert updated.id == original.id
    assert updated.expense_amount == 1250
    assert updated.expense_type == ExpenseType.LUNCH.value
    assert updated.expense_description == "обед с друзьями"
    assert (updated.expense_date.month, updated.expense_date.day) == (9, 5)
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT telegram_user_id, telegram_username, created_at FROM expenses").fetchone() == metadata
    await click(bot, update, context, f"e2:save:{flow_id}")
    assert len(await repository.list_recent_expenses(10)) == 1
    assert '"event": "expense_updated"' in bot._audit_logger.log_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_new_record_blocks_stale_edit(setup_bot):
    bot, repository, update, context = setup_bot
    await repository.append_expense(record())
    await bot.edit_last(update, context)
    draft = context.user_data[DRAFT_KEY]
    draft["expense_amount"] = "999"
    await repository.append_expense(record(900))
    query = await click(bot, update, context, f"e2:save:{draft['flow_id']}")
    assert "Ничего не сохранено" in query.edit_message_text.call_args.args[0]
    assert [item.expense_amount for item in await repository.list_recent_expenses(10)] == [650, 900]


@pytest.mark.asyncio
async def test_concurrent_delete_only_removes_one_record(setup_bot):
    _, repository, _, _ = setup_bot
    await repository.append_expense(record(100))
    await repository.append_expense(record(200))
    expected = await repository.get_last_expense()
    results = await asyncio.gather(
        repository.delete_last_expense(expected), repository.delete_last_expense(expected),
    )
    assert sorted(results) == [False, True]
    assert (await repository.get_last_expense()).expense_amount == 100


@pytest.mark.asyncio
async def test_existing_edit_blocks_stale_update_and_delete(setup_bot):
    _, repository, _, _ = setup_bot
    await repository.append_expense(record())
    expected = await repository.get_last_expense()
    assert await repository.update_last_expense(expected, record(1250))
    assert not await repository.update_last_expense(expected, record(999))
    assert not await repository.delete_last_expense(expected)
    assert (await repository.get_last_expense()).expense_amount == 1250


@pytest.mark.asyncio
async def test_cancel_edit_leaves_database_unchanged(setup_bot):
    bot, repository, update, context = setup_bot
    await repository.append_expense(record())
    original = await repository.get_last_expense()
    await bot.edit_last(update, context)
    draft = context.user_data[DRAFT_KEY]
    draft["expense_amount"] = "999"
    await bot.cancel(update, context)
    await click(bot, update, context, f"e2:save:{draft['flow_id']}")
    assert await repository.get_last_expense() == original
