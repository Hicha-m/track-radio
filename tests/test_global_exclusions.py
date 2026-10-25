import csv
import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.admin_sessions import _attempts
from app.models import GlobalExclusion, Play, Station, TrackMetadata
from app.services.exclusions import exclusion_id, normalized_pair
from app.services.track_identity import identity

AUTH = {"Authorization": "Bearer test-admin-token"}
SONG = {"artist": "SKYROCK", "title": "Les Dédicaces"}


@pytest.fixture
def library(db):
    station = Station(name="Radio", slug="radio", stream_url="https://example.com/live")
    db.add(station)
    db.flush()
    for artist, title in [
        ("skyrock", "Les Dedicaces"),
        (None, "Mouv’"),
        ("Other", "Mouv’"),
        ("Artist", "Visible"),
        ("Artist", "Visible (live)"),
    ]:
        key = identity(artist, title)
        db.add(TrackMetadata(id=key, artist=artist, title=title))
        for i in range(3):
            at = datetime.now(UTC) - timedelta(minutes=i + 1)
            db.add(
                Play(
                    station_id=station.id,
                    artist=artist,
                    title=title,
                    raw_metadata=title,
                    source="icy",
                    detected_at=at,
                    last_seen_at=at,
                )
            )
    db.commit()
    return station


def hide(client, track=SONG):
    result = client.post("/api/admin/exclusions", json=track, headers=AUTH)
    assert result.status_code == 200, result.text
    return result.json()


def titles(client, params=None):
    return {t["title"] for t in client.get("/api/stats/top", params=params or {}).json()}


def test_global_union_cache_ranking_exports_and_restore(client, db, library):
    # Fill all caches before hiding. Another browser never sends personal exclusions.
    before = client.get("/api/exclusions/revision").json()["revision"]
    assert "Les Dedicaces" in titles(client)
    assert client.get("/api/explore").json()["items"]
    assert client.get("/api/stats/genres").json()["total"] == 15
    data = hide(client)
    assert data["revision"] != before
    personal = json.dumps([{"artist": "Artist", "title": "Visible"}])
    assert titles(client, {"exclusions": personal}) == {"Mouv’", "Visible (live)"}
    assert titles(client) == {"Mouv’", "Visible", "Visible (live)"}
    with TestClient(client.app) as visitor:
        assert "Les Dedicaces" not in titles(visitor)
        assert "Visible" in titles(visitor)
        assert visitor.get("/api/stats/genres").json()["total"] == 12
        assert all(t["title"] != "Les Dedicaces" for t in visitor.get("/api/plays").json())
        page = visitor.get(
            "/api/explore", params={"view": "tracks", "page_size": 1, "sort": "title"}
        ).json()
        assert page["items"][0]["title"] == "Mouv’"
        for view in ("plays", "tracks"):
            for fmt in ("csv", "json"):
                response = visitor.get("/api/export", params={"view": view, "format": fmt})
                assert response.status_code == 200
                rows = (
                    response.json()["items"]
                    if fmt == "json"
                    else list(
                        csv.DictReader(
                            io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"
                        )
                    )
                )
                assert all(r["title"] != "Les Dedicaces" for r in rows)
    for _ in range(2):
        result = client.delete(f"/api/admin/exclusions/{data['id']}", headers=AUTH)
        assert result.status_code == 200
        assert result.json()["revision"] == before
    assert "Les Dedicaces" in titles(client)
    assert "Visible" not in titles(client, {"exclusions": personal})
    assert db.scalar(select(func.count()).select_from(Play)) == 15


def test_normalization_duplicates_unknown_artist_and_no_personal_limit(client, db, library):
    first = hide(client)
    assert hide(client, {"artist": "skyrock", "title": "Les Dedicaces"}) == first
    assert db.scalar(select(func.count()).select_from(GlobalExclusion)) == 1
    hide(client, {"title": "Mouv'"})
    result = client.get("/api/explore").json()["items"]
    assert any(t["title"] == "Mouv’" and t["artist"] == "Other" for t in result)
    assert not any(t["title"] == "Mouv’" and t["artist"] is None for t in result)
    # The global list is independent of the 50-entry personal query-string limit.
    for i in range(51):
        hide(client, {"title": f"Title {i}"})
    assert len(client.get("/api/admin/exclusions", headers=AUTH).json()["items"]) == 53


def test_direct_links_hidden_for_public_but_available_to_admin(client, library):
    hide(client)
    key = identity("skyrock", "Les Dedicaces")
    for path, params in [
        ("/api/catalog", {"artist": "skyrock", "title": "Les Dedicaces"}),
        (f"/api/catalog/{key}/radio", {}),
        (f"/api/catalog/{key}/preview", {}),
    ]:
        result = client.get(path, params=params)
        assert result.status_code == 404
        assert result.headers["cache-control"] == "no-store"
    assert (
        client.get(
            "/api/catalog", params={"artist": "skyrock", "title": "Les Dedicaces"}, headers=AUTH
        ).status_code
        == 200
    )
    assert client.get(f"/api/catalog/{key}/radio", headers=AUTH).json()["detections"] == 0
    assert len(client.get("/api/admin/plays?q=Les Dedicaces", headers=AUTH).json()["items"]) == 3
    assert client.get(f"/api/admin/catalog/{key}", headers=AUTH).status_code == 200
    # Catalogue identity collapses whitespace; an alternative query cannot expose the entry.
    assert (
        client.get(
            "/api/catalog", params={"artist": "skyrock", "title": "  Les Dedicaces  "}
        ).status_code
        == 404
    )


def test_admin_session_csrf_expiry_logout_and_validation(client, db, library):
    client.base_url = "https://testserver"
    _attempts.clear()
    assert client.get("/api/admin/exclusions").status_code == 401
    assert client.post("/api/admin/exclusions", json=SONG).status_code == 401
    login = client.post(
        "/api/admin/login",
        json={"token": "test-admin-token"},
        headers={"Origin": "https://testserver", "X-Admin-Login": "1"},
    )
    assert login.status_code == 200
    headers = {"Origin": "https://testserver", "X-CSRF-Token": login.json()["csrf_token"]}
    assert client.post("/api/admin/exclusions", json=SONG).status_code == 403
    result = client.post("/api/admin/exclusions", json=SONG, headers=headers)
    key = result.json()["id"]
    assert client.delete(f"/api/admin/exclusions/{key}").status_code == 403
    for track in [{"title": " "}, {"title": "a" * 501}, {"title": "x", "artist": "a" * 301}]:
        assert client.post("/api/admin/exclusions", json=track, headers=headers).status_code == 422
    assert client.post("/api/admin/logout", json={}, headers=headers).status_code == 200
    assert (
        client.post("/api/admin/exclusions", json={"title": "Other"}, headers=headers).status_code
        == 401
    )
    assert "Les Dedicaces" not in titles(client)
    assert db.get(GlobalExclusion, key) is not None
    _attempts.clear()


def test_migration_and_backup_preserve_hiding(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    from app.config import settings
    from scripts import transfer

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'migration.db'}")
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0008")
    command.upgrade(cfg, "head")
    engine = create_engine(settings.database_url)
    monkeypatch.setattr(transfer, "engine", engine)
    key = exclusion_id(**SONG)
    a, t = normalized_pair(**SONG)
    with engine.begin() as conn:
        assert conn.scalar(select(func.count()).select_from(GlobalExclusion)) == 0
        conn.execute(
            GlobalExclusion.__table__.insert(),
            dict(id=key, **SONG, artist_normalized=a, title_normalized=t),
        )
    backup = tmp_path / "backup.gz"
    transfer.export_data(backup)
    with engine.begin() as conn:
        conn.execute(GlobalExclusion.__table__.delete())
    transfer.import_data(backup)
    with engine.connect() as conn:
        assert conn.scalar(select(GlobalExclusion.id)) == key
    command.downgrade(cfg, "0008")
    command.upgrade(cfg, "head")
    engine.dispose()
