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


def test_create_station_generates_unique_slug(db, monkeypatch):
    monkeypatch.setattr("app.main.resolve_public", lambda url: "93.184.216.34")
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


def test_add_station_requires_admin(client):
    payload = {"name": "Test FM", "stream_url": "https://example.com/live"}
    assert client.post("/api/stations", json=payload).status_code == 401
    response = client.post(
        "/api/stations", json=payload, headers={"Authorization": "Bearer test-admin-token"}
    )
    assert response.status_code == 201
    assert (
        client.post(
            "/api/stations", json=payload, headers={"Authorization": "Bearer test-admin-token"}
        ).status_code
        == 409
    )


def test_reject_private_url_large_body_and_station_quota(client, monkeypatch):
    from app.config import settings

    headers = {"Authorization": "Bearer test-admin-token"}
    assert (
        client.post(
            "/api/stations",
            json={"name": "Local", "stream_url": "http://127.0.0.1"},
            headers=headers,
        ).status_code
        == 422
    )
    assert client.post("/api/stations", content=b"x" * 8193, headers=headers).status_code == 413
    monkeypatch.setattr(settings, "max_stations", 1)
    payload = {"name": "One", "stream_url": "https://example.com/live"}
    assert client.post("/api/stations", json=payload, headers=headers).status_code == 201
    payload["name"] = "Two"
    assert client.post("/api/stations", json=payload, headers=headers).status_code == 409


def test_empty_token_disables_writes(client, monkeypatch):
    from pydantic import SecretStr

    from app.config import settings

    monkeypatch.setattr(settings, "admin_token", SecretStr(""))
    assert (
        client.post(
            "/api/stations", json={"name": "Test", "stream_url": "https://example.com"}
        ).status_code
        == 503
    )
