from datetime import UTC, datetime, timedelta

from sqlalchemy import event

from app.models import Play, Station
from app.services.collector import record_metadata
from app.services.metadata import TrackMetadata


def test_unchanged_metadata_updates_existing_play(db):
    station = Station(name="Test", slug="test", stream_url="https://example.test/radio")
    db.add(station)
    db.commit()
    metadata = TrackMetadata("Artist", "Title", "Artist - Title", "icy")
    first, created = record_metadata(db, station, metadata)
    second, created_again = record_metadata(db, station, metadata)
    assert created is True
    assert created_again is False
    assert first.id == second.id
    assert db.query(Play).count() == 1


def test_changed_metadata_creates_play(db):
    station = Station(name="Test", slug="test", stream_url="https://example.test/radio")
    db.add(station)
    db.commit()
    record_metadata(db, station, TrackMetadata("Artist", "One", "Artist - One", "icy"))
    _, created = record_metadata(db, station, TrackMetadata("Artist", "Two", "Artist - Two", "icy"))
    assert created is True
    assert db.query(Play).count() == 2


def test_unchanged_track_throttles_writes_but_flushes_last_seen(db):
    station = Station(name="Test", slug="test", stream_url="https://example.test/radio")
    db.add(station)
    db.commit()
    metadata = TrackMetadata("Artist", "Title", "Artist - Title", "icy")
    first, _ = record_metadata(db, station, metadata)
    updates = []

    def observe(connection, cursor, statement, parameters, context, many):
        if statement.startswith("UPDATE plays"):
            updates.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", observe)
    record_metadata(db, station, metadata)
    assert updates == []
    first.last_seen_at = datetime.now(UTC) - timedelta(seconds=180)
    db.commit()
    updates.clear()
    record_metadata(db, station, metadata)
    assert len(updates) == 1
