from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from expense_bot.errors import ExpenseValidationError
from expense_bot.models import ExpenseRecognitionResult, ExpenseRecord
from expense_bot.services.llm_parser import OpenAICompatibleExpenseParser


class ExpenseRepository(Protocol):
    async def append_expense(self, expense: ExpenseRecord) -> None:
        ...


class ExpenseService:
    def __init__(
        self,
        parser: OpenAICompatibleExpenseParser,
        repository: ExpenseRepository,
    ) -> None:
        self._parser = parser
        self._repository = repository

    async def parse_expense(
        self,
        raw_text: str,
        telegram_user_id: int,
        telegram_username: str | None,
    ) -> ExpenseRecognitionResult:
        parsed_result = await self._parser.parse(raw_text)

        try:
            expense = ExpenseRecord.model_validate(
                {
                    "expense_amount": parsed_result.expense.expense_amount,
                    "expense_description": parsed_result.expense.expense_description,
                    "expense_type": parsed_result.expense.expense_type,
                    "expense_date": parsed_result.expense.expense_date,
                    "raw_text": raw_text,
                    "telegram_user_id": telegram_user_id,
                    "telegram_username": telegram_username,
                }
            )
        except ValidationError as exc:
            raise ExpenseValidationError(
                f"Не удалось провалидировать данные о трате: {exc}"
            ) from exc

        return ExpenseRecognitionResult(expense=expense, llm_call=parsed_result.llm_call)

    async def revise_expense(
        self,
        expense: ExpenseRecord,
        instruction: str,
    ) -> ExpenseRecognitionResult:
        revised_result = await self._parser.revise(expense, instruction)

        try:
            revised_expense = ExpenseRecord.model_validate(
                {
                    "expense_amount": revised_result.expense.expense_amount,
                    "expense_description": revised_result.expense.expense_description,
                    "expense_type": revised_result.expense.expense_type,
                    "expense_date": revised_result.expense.expense_date,
                    "raw_text": expense.raw_text,
                    "telegram_user_id": expense.telegram_user_id,
                    "telegram_username": expense.telegram_username,
                    "created_at": expense.created_at,
                }
            )
        except ValidationError as exc:
            raise ExpenseValidationError(
                f"Не удалось провалидировать исправленную трату: {exc}"
            ) from exc

        return ExpenseRecognitionResult(expense=revised_expense, llm_call=revised_result.llm_call)

    async def persist_expense(self, expense: ExpenseRecord) -> None:
        await self._repository.append_expense(expense)
