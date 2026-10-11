from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.main import create_station, health, make_slug, plays, top_tracks
from app.models import Play, Station
from app.schemas import StationCreate


def test_create_station_generates_unique_slug(db):
    first = create_station(
        StationCreate(name="Ma Radio Électro", stream_url="https://example.test/one"), db
    )
    second = create_station(
        StationCreate(name="Ma Radio Électro!", stream_url="https://example.test/two"), db
    )
    assert first.slug == "ma-radio-electro"
    assert second.slug == "ma-radio-electro-2"

    with pytest.raises(HTTPException) as error:
        create_station(
            StationCreate(name="Copie", stream_url="https://example.test/one"), db
        )
    assert error.value.status_code == 409


def test_make_slug():
    assert make_slug("Skyrock Rap & RnB") == "skyrock-rap-rnb"


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
