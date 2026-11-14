import sqlite3
import stat

import pytest

from scripts.backup_sqlite import backup


def test_live_backup_includes_committed_wal_data_and_preserves_source(tmp_path):
    source_path = tmp_path / "radio.db"
    target_path = tmp_path / "backups" / "radio.snapshot.db"
    target_path.parent.mkdir()
    source = sqlite3.connect(source_path)
    source.execute("PRAGMA journal_mode=WAL")
    source.execute("CREATE TABLE plays (id INTEGER PRIMARY KEY, title TEXT NOT NULL)")
    source.execute("CREATE INDEX plays_title ON plays(title)")
    source.execute("INSERT INTO plays(title) VALUES ('Premier'), ('Rediffusion')")
    source.commit()

    assert backup(source_path, target_path) == 1
    assert stat.S_IMODE(target_path.stat().st_mode) & 0o077 == 0

    with sqlite3.connect(target_path) as snapshot:
        assert snapshot.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert snapshot.execute("SELECT title FROM plays ORDER BY id").fetchall() == [
            ("Premier",),
            ("Rediffusion",),
        ]
        assert snapshot.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='plays_title'"
        ).fetchone() == ("plays_title",)
    assert source.execute("SELECT count(*) FROM plays").fetchone() == (2,)
    source.close()


def test_backup_refuses_existing_destination_without_overwriting_it(tmp_path):
    source_path = tmp_path / "radio.db"
    target_path = tmp_path / "snapshot.db"
    with sqlite3.connect(source_path) as source:
        source.execute("CREATE TABLE plays (title TEXT)")
        source.execute("INSERT INTO plays VALUES ('History retained')")
    target_path.write_bytes(b"existing archive")

    with pytest.raises(FileExistsError):
        backup(source_path, target_path)

    assert target_path.read_bytes() == b"existing archive"


def test_backup_removes_partial_destination_when_source_is_invalid(tmp_path):
    source_path = tmp_path / "not-a-database.db"
    target_path = tmp_path / "snapshot.db"
    source_path.write_text("not sqlite", encoding="utf-8")

    with pytest.raises(sqlite3.DatabaseError):
        backup(source_path, target_path)

    assert not target_path.exists()
