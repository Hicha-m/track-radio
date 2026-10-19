from datetime import UTC, datetime

from app.main import health, parse_station_id, plays, top_tracks
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
    assert stats[0].model_dump(exclude={"metadata"}) == {
        "artist": "Artist",
        "title": "Track",
        "plays": 1,
    }


def test_empty_dashboard_station_filter_means_all_stations():
    assert parse_station_id("") is None
    assert parse_station_id(None) is None
    assert parse_station_id("12") == 12


def test_dashboard_empty_filter_and_routes(client):
    for path in [
        "/",
        "/?station_id=&hours=24",
        "/health",
        "/api/plays",
        "/api/stats/top",
        "/static/style.css",
        "/static/dashboard.js",
        "/docs",
    ]:
        assert client.get(path).status_code == 200
    assert client.get("/?station_id=abc").status_code == 422


def test_catalog_writes_require_admin_and_bound_body(client, monkeypatch):
    from pydantic import SecretStr

    from app.config import settings

    url = "/api/catalog/unknown/correction"
    assert client.post(url, json={}).status_code == 401
    assert client.post(url, content=b"x" * 8193).status_code == 413
    monkeypatch.setattr(settings, "admin_token", SecretStr(""))
    assert client.post(url, json={}).status_code == 503
