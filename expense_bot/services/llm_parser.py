from __future__ import annotations

import json
import re
from time import perf_counter
from typing import Any, Literal
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
from dateparser.search import search_dates

from expense_bot.categories import (
    ExpenseType,
    EXPENSE_TYPE_VALUES,
    detect_explicit_expense_type,
    resolve_expense_type,
)
from expense_bot.errors import LLMParseError
from expense_bot.models import LLMCallMetrics, LLMUsage, ParsedExpense, ParsedExpenseResult

DATE_CUE_PATTERN = re.compile(
    r"\b(сегодня|вчера|позавчера|завтра|\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?|январ|феврал|март|апрел|мая|июн|июл|август|сентябр|октябр|ноябр|декабр)\b",
    re.IGNORECASE,
)
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]


class OpenAICompatibleExpenseParser:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None,
        timezone: ZoneInfo,
        timeout_seconds: int = 20,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timezone = timezone
        self._timeout_seconds = timeout_seconds
        self._reasoning_effort = reasoning_effort

    async def parse(self, raw_text: str) -> ParsedExpenseResult:
        explicit_expense_type = detect_explicit_expense_type(raw_text)
        explicit_expense_date = self._extract_explicit_date(raw_text)
        today = datetime.now(self._timezone).date()

        response_payload, llm_call = await self._send_responses_request(
            system_prompt=self._build_parse_system_prompt(today),
            user_prompt=self._build_parse_user_prompt(
                raw_text=raw_text,
                explicit_expense_type=explicit_expense_type,
                explicit_expense_date=explicit_expense_date,
            ),
        )

        return self._build_parsed_result(
            response_payload=response_payload,
            llm_call=llm_call,
            fallback_description=raw_text,
            default_date=today,
            explicit_expense_type=explicit_expense_type,
            explicit_expense_date=explicit_expense_date,
        )

    async def revise(self, expense: ParsedExpense | Any, instruction: str) -> ParsedExpenseResult:
        explicit_expense_type = detect_explicit_expense_type(instruction)
        explicit_expense_date = self._extract_explicit_date(instruction)
        today = datetime.now(self._timezone).date()

        response_payload, llm_call = await self._send_responses_request(
            system_prompt=self._build_revision_system_prompt(today),
            user_prompt=self._build_revision_user_prompt(
                expense=expense,
                instruction=instruction,
                explicit_expense_type=explicit_expense_type,
                explicit_expense_date=explicit_expense_date,
            ),
        )

        return self._build_parsed_result(
            response_payload=response_payload,
            llm_call=llm_call,
            fallback_description=expense.expense_description,
            default_date=expense.expense_date,
            explicit_expense_type=explicit_expense_type,
            explicit_expense_date=explicit_expense_date,
        )

    async def _send_responses_request(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[dict[str, Any], LLMCallMetrics]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        request_payload: dict[str, Any] = {
            "model": self._model,
            "instructions": system_prompt,
            "input": user_prompt,
            "text": {"format": {"type": "json_object"}},
            "temperature": 0,
        }
        if self._reasoning_effort is not None:
            request_payload["reasoning"] = {"effort": self._reasoning_effort}

        timeout = httpx.Timeout(self._timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            started_at = perf_counter()
            try:
                response = await client.post(
                    f"{self._base_url}/v1/responses",
                    headers=headers,
                    json=request_payload,
                )
                response.raise_for_status()
                response_payload = response.json()
                llm_call = self._build_llm_call_metrics(response_payload, started_at)
                return response_payload, llm_call
            except httpx.TimeoutException as exc:
                raise LLMParseError(
                    f"Модель не успела ответить за {self._timeout_seconds} сек. "
                    "Увеличь REQUEST_TIMEOUT_SECONDS или упрости запрос."
                ) from exc
            except Exception as exc:
                raise LLMParseError(
                    "Не удалось получить ответ от OpenAI-compatible endpoint. "
                    "Проверь, что локальный сервер доступен и поддерживает POST /v1/responses."
                ) from exc

    def _build_parsed_result(
        self,
        response_payload: dict[str, Any],
        llm_call: LLMCallMetrics,
        fallback_description: str,
        default_date: date,
        explicit_expense_type: ExpenseType | None,
        explicit_expense_date: date | None,
    ) -> ParsedExpenseResult:
        try:
            content = self._extract_output_text(response_payload)
            payload = self._load_json(content)

            payload["expense_type"] = (
                explicit_expense_type.value if explicit_expense_type else payload.get("expense_type")
            )
            payload["expense_description"] = payload.get("expense_description") or fallback_description
            payload["expense_date"] = (
                explicit_expense_date.isoformat()
                if explicit_expense_date
                else payload.get("expense_date") or default_date.isoformat()
            )

            if payload.get("expense_type"):
                payload["expense_type"] = resolve_expense_type(payload["expense_type"])

            expense = ParsedExpense.model_validate(payload)
        except LLMParseError as exc:
            raise LLMParseError(str(exc), llm_call=llm_call) from exc
        except ValueError as exc:
            raise LLMParseError(str(exc), llm_call=llm_call) from exc
        except Exception as exc:
            raise LLMParseError(
                f"Не удалось провалидировать ответ модели: {exc}",
                llm_call=llm_call,
            ) from exc

        return ParsedExpenseResult(expense=expense, llm_call=llm_call)

    def _build_parse_system_prompt(self, today: date) -> str:
        expense_types = ", ".join(EXPENSE_TYPE_VALUES)
        return (
            "Ты извлекаешь структурированные данные о трате из сообщения пользователя. "
            "Верни строго JSON без markdown и без пояснений. "
            f"Допустимые типы трат: {expense_types}. "
            f"Сегодняшняя дата: {today.isoformat()}. "
            "Верни поля expense_amount, expense_description, expense_type, expense_date. "
            "expense_amount должен быть числом. "
            "expense_description должен быть коротким описанием траты. "
            "expense_type должен быть одним из допустимых типов трат. "
            "expense_date должен быть датой в формате YYYY-MM-DD. "
            "Если дата не указана, используй сегодняшнюю. "
            "Если тип траты указан явно, сохрани именно его."
        )

    def _build_parse_user_prompt(
        self,
        raw_text: str,
        explicit_expense_type: ExpenseType | None,
        explicit_expense_date: date | None,
    ) -> str:
        expense_type_hint = explicit_expense_type.value if explicit_expense_type else "не указан"
        expense_date_hint = explicit_expense_date.isoformat() if explicit_expense_date else "не указана"
        return (
            f"Сообщение пользователя: {raw_text}\n"
            f"Явный тип траты: {expense_type_hint}\n"
            f"Явная дата траты: {expense_date_hint}"
        )

    def _build_revision_system_prompt(self, today: date) -> str:
        expense_types = ", ".join(EXPENSE_TYPE_VALUES)
        return (
            "Ты исправляешь уже распознанную трату по инструкции пользователя. "
            "Верни строго JSON без markdown и без пояснений. "
            f"Допустимые типы трат: {expense_types}. "
            f"Сегодняшняя дата: {today.isoformat()}. "
            "Верни полный объект с полями expense_amount, expense_description, expense_type, expense_date. "
            "Если пользователь просит изменить только одно поле, остальные поля сохрани без изменений. "
            "Фразы вроде 'нет, исправь' или 'неверно' означают, что нужно применить исправление, а не отменить запись. "
            "expense_amount должен быть числом. "
            "expense_type должен быть одним из допустимых типов трат. "
            "expense_date должен быть в формате YYYY-MM-DD."
        )

    def _build_revision_user_prompt(
        self,
        expense: ParsedExpense | Any,
        instruction: str,
        explicit_expense_type: ExpenseType | None,
        explicit_expense_date: date | None,
    ) -> str:
        expense_type_hint = explicit_expense_type.value if explicit_expense_type else "не указан"
        expense_date_hint = explicit_expense_date.isoformat() if explicit_expense_date else "не указана"
        return (
            "Текущая распознанная трата:\n"
            f"- expense_amount: {expense.expense_amount}\n"
            f"- expense_description: {expense.expense_description}\n"
            f"- expense_type: {expense.expense_type.value}\n"
            f"- expense_date: {expense.expense_date.isoformat()}\n\n"
            f"Инструкция пользователя: {instruction}\n"
            f"Явный тип траты в инструкции: {expense_type_hint}\n"
            f"Явная дата в инструкции: {expense_date_hint}"
        )

    def _load_json(self, content: str) -> dict[str, Any]:
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if not match:
                raise LLMParseError("LLM не вернула JSON.")
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise LLMParseError("LLM вернула некорректный JSON.") from exc

        if not isinstance(data, dict):
            raise LLMParseError("LLM вернула JSON не в формате объекта.")
        return data

    def _extract_output_text(self, payload: dict[str, Any]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        collected_parts: list[str] = []
        for item in payload.get("output", []):
            if item.get("type") != "message":
                continue
            for content_item in item.get("content", []):
                content_type = content_item.get("type")
                if content_type not in {"output_text", "text"}:
                    continue
                text = content_item.get("text")
                if isinstance(text, str) and text.strip():
                    collected_parts.append(text)

        if collected_parts:
            return "\n".join(collected_parts)

        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {})
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content

        raise LLMParseError(
            "OpenAI-compatible endpoint вернул ответ без текстового содержимого."
        )

    def _extract_explicit_date(self, raw_text: str) -> date | None:
        normalized = raw_text.lower().replace("ё", "е")
        if not DATE_CUE_PATTERN.search(normalized):
            return None

        matches = search_dates(
            raw_text,
            languages=["ru", "en"],
            settings={
                "DATE_ORDER": "DMY",
                "PREFER_DATES_FROM": "past",
                "RELATIVE_BASE": datetime.now(self._timezone),
            },
        )
        if not matches:
            return None

        for source_text, parsed_datetime in matches:
            if DATE_CUE_PATTERN.search(source_text.lower().replace("ё", "е")):
                return parsed_datetime.date()
        return matches[0][1].date()

    def _build_llm_call_metrics(
        self,
        response_payload: dict[str, Any],
        started_at: float,
    ) -> LLMCallMetrics:
        return LLMCallMetrics(
            model=self._model,
            latency_ms=max(1, int((perf_counter() - started_at) * 1000)),
            reasoning_effort=self._reasoning_effort,
            usage=self._extract_usage(response_payload),
        )

    def _extract_usage(self, response_payload: dict[str, Any]) -> LLMUsage:
        usage = response_payload.get("usage")
        if not isinstance(usage, dict):
            return LLMUsage()

        input_details = usage.get("input_tokens_details")
        output_details = usage.get("output_tokens_details")
        if not isinstance(input_details, dict):
            input_details = {}
        if not isinstance(output_details, dict):
            output_details = {}

        return LLMUsage(
            input_tokens=self._coerce_int(usage.get("input_tokens"))
            or self._coerce_int(usage.get("prompt_tokens")),
            output_tokens=self._coerce_int(usage.get("output_tokens"))
            or self._coerce_int(usage.get("completion_tokens")),
            total_tokens=self._coerce_int(usage.get("total_tokens")),
            reasoning_tokens=self._coerce_int(output_details.get("reasoning_tokens"))
            or self._coerce_int(usage.get("reasoning_tokens")),
        )

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None


OllamaExpenseParser = OpenAICompatibleExpenseParser
