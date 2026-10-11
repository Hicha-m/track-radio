from datetime import UTC, datetime

from app.main import create_station, health, parse_station_id, plays, top_tracks
from app.models import Play, Station
from app.schemas import StationCreate


def test_filtered_api_functions(db):
    station = Station(name="Test FM", slug="test-fm", stream_url="https://example.test/live")
    db.add(station)
    db.commit()
    now = datetime.now(UTC)
    db.add(
        Play(
            station_id=station.id,
            artist="Artist",
            title="Track",
            raw_metadata="Artist - Track",
            detected_at=now,
            last_seen_at=now,
            source="icy",
        )
    )
    db.commit()

    assert health(db) == {"status": "ok"}
    result = plays(db, station_id=station.id, limit=100)
    assert result[0].title == "Track"
    stats = top_tracks(db, station_id=station.id, limit=20)
    assert stats[0].model_dump() == {"artist": "Artist", "title": "Track", "plays": 1}


def test_create_station_generates_unique_slug(db):
    first = create_station(
        StationCreate(name="Radio Etoile", stream_url="https://example.test/live"), db
    )
    second = create_station(
        StationCreate(name="Radio Étoile", stream_url="https://example.test/live-2"), db
    )

    assert first.slug == "radio-etoile"
    assert second.slug == "radio-etoile-2"
    assert second.enabled is True


def test_empty_dashboard_station_filter_means_all_stations():
    assert parse_station_id("") is None
    assert parse_station_id(None) is None
    assert parse_station_id("12") == 12
