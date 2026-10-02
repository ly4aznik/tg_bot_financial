import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from expense_bot.backup import create_backup, main


NOW = datetime(2026, 10, 2, 9, tzinfo=timezone.utc)


def make_database(path):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE expenses (amount INTEGER)")
        connection.execute("INSERT INTO expenses VALUES (650)")


def test_backup_includes_committed_wal_data(tmp_path):
    source_path = tmp_path / "expenses.sqlite3"
    make_database(source_path)
    with sqlite3.connect(source_path) as source:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute("INSERT INTO expenses VALUES (1250)")
        source.commit()
        backup = create_backup(source_path, tmp_path / "backups", NOW)
    with sqlite3.connect(backup) as snapshot:
        assert snapshot.execute("SELECT amount FROM expenses").fetchall() == [(650,), (1250,)]
        assert snapshot.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert list(backup.parent.glob("*.tmp")) == []


def test_backups_preserve_previous_snapshots(tmp_path):
    source = tmp_path / "expenses.sqlite3"
    directory = tmp_path / "backups"
    make_database(source)
    first = create_backup(source, directory, NOW)
    with sqlite3.connect(source) as connection:
        connection.execute("INSERT INTO expenses VALUES (1250)")
    second = create_backup(source, directory, NOW + timedelta(days=7))
    with sqlite3.connect(first) as snapshot:
        assert snapshot.execute("SELECT COUNT(*) FROM expenses").fetchone() == (1,)
    with sqlite3.connect(second) as snapshot:
        assert snapshot.execute("SELECT COUNT(*) FROM expenses").fetchone() == (2,)


def test_missing_source_does_not_create_empty_database_or_completed_backup(tmp_path):
    source = tmp_path / "missing.sqlite3"
    directory = tmp_path / "backups"
    with pytest.raises(sqlite3.OperationalError):
        create_backup(source, directory, NOW)
    assert not source.exists()
    assert list(directory.iterdir()) == []
    make_database(source)
    assert create_backup(source, directory, NOW).exists()


def test_corrupt_source_is_not_published(tmp_path):
    source = tmp_path / "corrupt.sqlite3"
    source.write_bytes(b"this is not a sqlite database")
    directory = tmp_path / "backups"
    with pytest.raises(sqlite3.DatabaseError):
        create_backup(source, directory, NOW)
    assert list(directory.iterdir()) == []


def test_unfinished_and_unrelated_files_do_not_delay_backup(tmp_path):
    source = tmp_path / "expenses.sqlite3"
    make_database(source)
    directory = tmp_path / "backups"
    directory.mkdir()
    (directory / ".backup-interrupted.tmp").write_bytes(b"partial")
    (directory / "expenses-manual.sqlite3").write_bytes(b"unrelated")
    assert create_backup(source, directory, NOW).exists()


def test_main_creates_one_backup_and_exits(tmp_path, monkeypatch):
    source = tmp_path / "expenses.sqlite3"
    directory = tmp_path / "backups"
    make_database(source)
    monkeypatch.setenv("SQLITE_DATABASE_PATH", str(source))
    monkeypatch.setenv("SQLITE_BACKUP_DIRECTORY", str(directory))
    main()
    assert len(list(directory.glob("*.sqlite3"))) == 1


def test_main_propagates_failure_to_systemd(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_DATABASE_PATH", str(tmp_path / "missing.sqlite3"))
    monkeypatch.setenv("SQLITE_BACKUP_DIRECTORY", str(tmp_path / "backups"))
    with pytest.raises(sqlite3.OperationalError):
        main()
