from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError
from requests import RequestException, Timeout as RequestsTimeout

from expense_bot.errors import GoogleSheetsError
from expense_bot.models import ExpenseRecord

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
HEADER_RANGE = "B4:E4"
DATA_READ_RANGE = "B5:E"
DATA_START_ROW = 5
EXPECTED_HEADERS = ["Дата", "Сумма", "Описание", "Категория"]
RETRYABLE_API_STATUS_CODES = {408, 429, 500, 502, 503, 504}
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 2.0
LOGGER = logging.getLogger(__name__)


class GoogleSheetsExpenseRepository:
    def __init__(
        self,
        service_account_json: Path,
        spreadsheet_id: str,
        worksheet_name: str,
        timeout_seconds: int = 30,
        retry_attempts: int = DEFAULT_RETRY_ATTEMPTS,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    ) -> None:
        self._service_account_json = Path(service_account_json)
        self._spreadsheet_id = spreadsheet_id
        self._worksheet_name = worksheet_name
        self._timeout_seconds = timeout_seconds
        self._retry_attempts = max(1, retry_attempts)
        self._retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._worksheet: gspread.Worksheet | None = None

        if not self._service_account_json.exists():
            raise GoogleSheetsError(
                f"Файл сервисного аккаунта не найден: {self._service_account_json}"
            )

    async def append_expense(self, expense: ExpenseRecord) -> None:
        await asyncio.to_thread(self._append_expense_sync, expense)

    def _append_expense_sync(self, expense: ExpenseRecord) -> None:
        last_error: Exception | None = None

        for attempt in range(1, self._retry_attempts + 1):
            try:
                worksheet = self._get_worksheet(force_refresh=attempt > 1)
                current_values = worksheet.get(DATA_READ_RANGE)
                target_row = self._find_next_empty_row(current_values)
                self._ensure_row_capacity(worksheet, target_row)
                row_values = self._build_expense_row(expense)
                LOGGER.info(
                    "Writing expense to Google Sheets: worksheet=%s row=%s values=%s attempt=%s/%s",
                    self._worksheet_name,
                    target_row,
                    row_values,
                    attempt,
                    self._retry_attempts,
                )
                worksheet.update(
                    values=[row_values],
                    range_name=f"B{target_row}:E{target_row}",
                    value_input_option="USER_ENTERED",
                )
                return
            except GoogleSheetsError:
                raise
            except APIError as exc:
                if self._is_retryable_api_error(exc):
                    last_error = exc
                    self._handle_retryable_failure(
                        exc=exc,
                        attempt=attempt,
                        operation="append_expense",
                    )
                    continue
                raise GoogleSheetsError(self._build_api_error_message(exc)) from exc
            except RequestsTimeout as exc:
                last_error = exc
                self._handle_retryable_failure(
                    exc=exc,
                    attempt=attempt,
                    operation="append_expense",
                )
                continue
            except RequestException as exc:
                last_error = exc
                self._handle_retryable_failure(
                    exc=exc,
                    attempt=attempt,
                    operation="append_expense",
                )
                continue
            except Exception as exc:
                LOGGER.exception("Unexpected Google Sheets append error")
                raise GoogleSheetsError(
                    "Не удалось записать расход в Google Sheets."
                ) from exc

        if isinstance(last_error, RequestsTimeout):
            raise GoogleSheetsError(
                f"Google Sheets не ответил за {self._timeout_seconds} сек. Попробуй еще раз."
            ) from last_error
        if isinstance(last_error, RequestException):
            raise GoogleSheetsError(
                "Ошибка сети при записи в Google Sheets. Попробуй еще раз."
            ) from last_error
        if isinstance(last_error, APIError):
            raise GoogleSheetsError(self._build_api_error_message(last_error)) from last_error
        raise GoogleSheetsError("Не удалось записать расход в Google Sheets.")

    def _get_worksheet(self, force_refresh: bool = False) -> gspread.Worksheet:
        if self._worksheet is not None and not force_refresh:
            return self._worksheet

        credentials = Credentials.from_service_account_file(
            str(self._service_account_json),
            scopes=SCOPES,
        )
        client = gspread.authorize(credentials)
        client.set_timeout((5, self._timeout_seconds))
        spreadsheet = client.open_by_key(self._spreadsheet_id)

        try:
            worksheet = spreadsheet.worksheet(self._worksheet_name)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(
                title=self._worksheet_name,
                rows=1000,
                cols=8,
            )

        self._ensure_headers(worksheet)
        self._worksheet = worksheet
        return worksheet

    def _ensure_headers(self, worksheet: gspread.Worksheet) -> None:
        headers = worksheet.get(HEADER_RANGE)
        if not headers or self._is_empty_row(headers[0]):
            worksheet.update(
                values=[EXPECTED_HEADERS],
                range_name=HEADER_RANGE,
                value_input_option="USER_ENTERED",
            )
            return

        actual_headers = headers[0]
        normalized_headers = actual_headers + [""] * (len(EXPECTED_HEADERS) - len(actual_headers))
        if normalized_headers[: len(EXPECTED_HEADERS)] != EXPECTED_HEADERS:
            raise GoogleSheetsError(
                "Структура листа не совпадает с ожидаемой. "
                f"В диапазоне {HEADER_RANGE} ожидались заголовки: {', '.join(EXPECTED_HEADERS)}"
            )

    def _ensure_row_capacity(self, worksheet: gspread.Worksheet, target_row: int) -> None:
        if target_row <= worksheet.row_count:
            return

        rows_to_add = target_row - worksheet.row_count
        LOGGER.info(
            "Extending worksheet rows: worksheet=%s current_rows=%s target_row=%s add_rows=%s",
            self._worksheet_name,
            worksheet.row_count,
            target_row,
            rows_to_add,
        )
        worksheet.add_rows(rows_to_add)

    def _handle_retryable_failure(
        self,
        exc: Exception,
        attempt: int,
        operation: str,
    ) -> None:
        LOGGER.warning(
            "Retryable Google Sheets error during %s (attempt %s/%s): %s",
            operation,
            attempt,
            self._retry_attempts,
            exc,
            exc_info=True,
        )
        self._worksheet = None
        if attempt >= self._retry_attempts:
            return
        sleep_seconds = self._retry_backoff_seconds * attempt
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    @staticmethod
    def _is_retryable_api_error(exc: APIError) -> bool:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        return isinstance(status_code, int) and status_code in RETRYABLE_API_STATUS_CODES

    @staticmethod
    def _build_api_error_message(exc: APIError) -> str:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        default_message = "Google Sheets API вернул ошибку при записи расхода."

        if response is None:
            return default_message

        try:
            payload = response.json()
        except Exception:
            payload = None

        if isinstance(payload, dict):
            error_payload = payload.get("error")
            if isinstance(error_payload, dict):
                message = error_payload.get("message")
                if isinstance(message, str) and message.strip():
                    if status_code:
                        return f"Google Sheets API вернул ошибку {status_code}: {message}"
                    return f"Google Sheets API вернул ошибку: {message}"

        if status_code:
            return f"Google Sheets API вернул ошибку {status_code} при записи расхода."
        return default_message

    def _build_expense_row(self, expense: ExpenseRecord) -> list[Any]:
        return [
            expense.expense_date.isoformat(),
            float(expense.expense_amount),
            expense.expense_description,
            expense.expense_type.value,
        ]

    def _find_next_empty_row(self, values: list[list[str]]) -> int:
        for index, row in enumerate(values):
            if self._is_empty_row(row):
                return DATA_START_ROW + index
        return DATA_START_ROW + len(values)

    @staticmethod
    def _is_empty_row(row: list[str]) -> bool:
        return all(not str(cell).strip() for cell in row)

