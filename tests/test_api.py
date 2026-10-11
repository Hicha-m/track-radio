from datetime import UTC, datetime

from app.main import health, plays, top_tracks
from app.models import Play, Station



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
