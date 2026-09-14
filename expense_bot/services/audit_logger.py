from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

LOGGER = logging.getLogger(__name__)


class AuditLogger:
    """Append structured interaction events to a JSONL audit log."""

    def __init__(self, log_path: Path) -> None:
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @property
    def log_path(self) -> Path:
        return self._log_path

    def log_event(self, event: str, **payload: Any) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **payload,
        }
        line = json.dumps(record, ensure_ascii=False, default=self._serialize)
        try:
            with self._lock:
                with self._log_path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError as exc:
            # Auditing is auxiliary: a locked or read-only log must never break
            # the expense-entry conversation.
            LOGGER.warning("Audit event was not written to %s: %s", self._log_path, exc)

    def _serialize(self, value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")
