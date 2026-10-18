import gzip
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.config import settings
from app.models import Play, Station
from app.services.collector import record_metadata
from app.services.maintenance import archive_old_plays
from app.services.metadata import TrackMetadata


def old_play(db):
    station = Station(name="Archive FM", slug="archive", stream_url="https://example.com")
    db.add(station)
    db.commit()
    play, _ = record_metadata(db, station, TrackMetadata("Artist", "Old", "Artist - Old", "icy"))
    play.detected_at = play.last_seen_at = datetime.now(UTC) - timedelta(days=200)
    db.commit()
    return play.id


def test_archive_preserves_data_before_removing_active_rows(db, tmp_path, monkeypatch):
    play_id = old_play(db)
    monkeypatch.setattr(settings, "archive_directory", str(tmp_path))
    monkeypatch.setattr("app.services.maintenance.SessionLocal", lambda: db)
    archive_old_plays()
    files = list(tmp_path.glob("*.jsonl.gz"))
    assert len(files) == 1
    with gzip.open(files[0], "rt") as source:
        assert json.loads(source.readline())["id"] == play_id
    assert db.query(Play).count() == 0


def test_failed_archive_keeps_database_rows(db, tmp_path, monkeypatch):
    old_play(db)
    monkeypatch.setattr(settings, "archive_directory", str(tmp_path))
    monkeypatch.setattr("app.services.maintenance.SessionLocal", lambda: db)

    def fail(*args):
        raise OSError("Disk full")

    monkeypatch.setattr("app.services.maintenance.os.replace", fail)
    with pytest.raises(OSError):
        archive_old_plays()
    assert db.query(Play).count() == 1
