"""Create one verified SQLite backup; scheduling is handled by systemd."""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


LOGGER = logging.getLogger(__name__)
BACKUP_TIME_FORMAT = "%Y%m%dT%H%M%S%fZ"


def create_backup(database_path: Path, backup_directory: Path, now: datetime) -> Path:
    """Publish a verified snapshot atomically; never create a missing source DB."""
    backup_directory.mkdir(parents=True, exist_ok=True)
    timestamp = now.astimezone(timezone.utc).strftime(BACKUP_TIME_FORMAT)
    destination = backup_directory / f"expenses-{timestamp}.sqlite3"
    with tempfile.NamedTemporaryFile(
        dir=backup_directory, prefix=".backup-", suffix=".tmp", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        source_uri = database_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True, timeout=30)) as source:
            with closing(sqlite3.connect(temporary_path)) as target:
                source.backup(target, pages=256)
                result = target.execute("PRAGMA integrity_check").fetchall()
                if result != [("ok",)]:
                    raise RuntimeError(f"Backup integrity check failed: {result}")
        with temporary_path.open("r+b") as snapshot:
            os.fsync(snapshot.fileno())
        temporary_path.replace(destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    LOGGER.info("SQLite backup saved: %s", destination)
    return destination


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        level=logging.INFO,
    )
    database_path = Path(os.environ.get("SQLITE_DATABASE_PATH", "data/expenses.sqlite3"))
    backup_directory = Path(
        os.environ.get("SQLITE_BACKUP_DIRECTORY", "/mnt/storage/shared/backup/tg_bot_financial")
    )
    LOGGER.info("SQLite backup: %s -> %s", database_path, backup_directory)
    create_backup(database_path, backup_directory, datetime.now(timezone.utc))


if __name__ == "__main__":
    main()
