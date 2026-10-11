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
