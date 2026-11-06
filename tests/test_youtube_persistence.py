import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import ServiceQuota, WorkerEvent, WorkerState, YouTubeMatch
from app.services import youtube
from app.services.catalog import ensure_entry
from app.services.supervision import report

AUTH = {"Authorization": "Bearer test-admin-token"}
VIDEO = {"id": "abcdefghijk", "title": "Artist Song", "channel": "Artist"}


@pytest.fixture
def remote(monkeypatch):
    calls = []

    async def search(artist, title):
        calls.append((artist, title))
        return {"status": "matched", "candidates": [VIDEO]}

    monkeypatch.setattr(settings, "youtube_api_key", SecretStr("test-only"))
    monkeypatch.setattr(youtube, "search_remote", search)
    return calls


def test_persistent_results_quota_and_daily_reset(db, remote, monkeypatch):
    monkeypatch.setattr(settings, "youtube_searches_per_day", 1)
    first = asyncio.run(youtube.discover("Artist", "Song", db))
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    with factory() as other:
        second = asyncio.run(youtube.discover("Artist", "Song", other))
        assert second == first and len(remote) == 1
        with pytest.raises(HTTPException) as error:
            asyncio.run(youtube.discover("Other", "Song", other))
        assert error.value.status_code == 429
    quota = db.get(ServiceQuota, "youtube")
    db.refresh(quota)
    assert quota.used == 1
    quota.day = "2000-01-01"
    db.commit()
    asyncio.run(youtube.discover("Other", "Song", db))
    assert len(remote) == 2
    db.refresh(quota)
    assert quota.used == 1


def test_error_backoff_shared_and_scrubbed(db, remote, monkeypatch):
    async def fail(*args):
        raise HTTPException(503, "Service indisponible.")

    monkeypatch.setattr(youtube, "search_remote", fail)
    with pytest.raises(HTTPException):
        asyncio.run(youtube.discover("Artist", "Song", db))
    factory = sessionmaker(bind=db.get_bind())
    with factory() as other:
        with pytest.raises(HTTPException) as error:
            asyncio.run(youtube.discover("Another", "Song", other))
        assert error.value.status_code == 429
    match = db.scalar(select(YouTubeMatch).where(YouTubeMatch.artist == "Artist"))
    assert match.status == "error" and match.retry_at is not None
    assert match.lease_until is None


def test_admin_choice_independent_and_conflict_protected(db, client, remote):
    entry = ensure_entry(db, "Artist", "Song")
    entry.selected = {
        "provider": "deezer",
        "provider_id": "42",
        "cover_url": "https://example.test/image",
    }
    db.commit()
    url = f"/api/catalog/{entry.id}/youtube"
    assert client.get(url + "/state").status_code == 401
    first = client.get(url).json()
    assert first["selected"] == VIDEO
    assert (
        client.post(
            url + "/choice",
            json={"candidate_id": "ABCDEFGHIJK", "revision": first["revision"]},
            headers=AUTH,
        ).status_code
        == 422
    )
    chosen = client.post(
        url + "/choice",
        json={"candidate_id": VIDEO["id"], "revision": first["revision"]},
        headers=AUTH,
    )
    assert chosen.status_code == 200 and chosen.json()["manual"] is True
    assert (
        client.post(
            url + "/choice",
            json={"candidate_id": None, "revision": first["revision"]},
            headers=AUTH,
        ).status_code
        == 409
    )
    db.refresh(entry)
    assert entry.selected["cover_url"] == "https://example.test/image"
    refreshed = client.post(url + "/search", json={}, headers=AUTH).json()
    assert refreshed["manual"] and refreshed["selected"] == VIDEO
    removed = client.post(
        url + "/choice",
        json={"candidate_id": None, "revision": refreshed["revision"]},
        headers=AUTH,
    ).json()
    assert removed["status"] == "rejected"
    assert client.get(url).json()["selected"] is None


def test_manual_choice_wins_over_inflight_search(db, remote, monkeypatch):
    asyncio.run(youtube.discover("Artist", "Song", db))
    match = db.scalar(select(YouTubeMatch))
    factory = sessionmaker(bind=db.get_bind())

    async def concurrent(*args):
        with factory() as other:
            row = other.get(YouTubeMatch, match.track_id)
            row.revision += 1
            row.selected = None
            row.manual = True
            row.status = "rejected"
            row.lease_until = None
            other.commit()
        return {"status": "matched", "candidates": [VIDEO]}

    monkeypatch.setattr(youtube, "search_remote", concurrent)
    result = asyncio.run(youtube.discover("Artist", "Song", db, refresh=True))
    assert result["manual"] and result["selected"] is None


def test_worker_only_requested_tracks_and_respects_exclusions(db, remote):
    ensure_entry(db, "Untouched", "Old catalogue")
    entry = ensure_entry(db, "Artist", "Song")
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    assert asyncio.run(youtube.worker_round(factory)) is False
    youtube.queue(db, entry.id, entry.artist, entry.title)
    db.commit()
    assert asyncio.run(youtube.worker_round(factory)) is True
    assert len(remote) == 1


def test_worker_journal_bounded_no_arbitrary_payload_and_stale_state(db, client):
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    for _ in range(205):
        report(factory, "collector", "completed", total=5, completed=4, failed=1)
    assert db.scalar(select(func.count()).select_from(WorkerEvent)) == 200
    with pytest.raises(ValueError):
        report(factory, "collector", "secret upstream payload")
    state = db.get(WorkerState, "collector")
    state.heartbeat_at = datetime.now(UTC) - timedelta(minutes=5)
    db.commit()
    assert client.get("/api/admin/workers").status_code == 401
    data = client.get("/api/admin/workers", headers=AUTH).json()
    assert data["items"][0]["state"] == "stale"
    assert data["items"][0]["completed"] == 4
    assert len(data["events"]) == 100


def test_shared_quota_is_atomic_across_concurrent_sessions(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from app.database import make_engine
    from app.models import Base

    engine = make_engine(f"sqlite:///{tmp_path / 'quota.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "youtube_api_key", SecretStr("test-only"))
    monkeypatch.setattr(settings, "youtube_searches_per_day", 3)

    async def search(*args):
        await asyncio.sleep(0.02)
        return {"status": "matched", "candidates": [VIDEO]}

    monkeypatch.setattr(youtube, "search_remote", search)

    def run(index):
        with factory() as session:
            try:
                asyncio.run(youtube.discover("Artist", f"Song {index}", session))
                return 200
            except HTTPException as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=6) as executor:
        statuses = list(executor.map(run, range(10)))
    assert statuses.count(200) == 3
    assert statuses.count(429) == 7
    with factory() as session:
        assert session.get(ServiceQuota, "youtube").used == 3
    engine.dispose()


def test_video_admin_and_worker_cannot_bypass_personal_or_global_exclusions(db, client, remote):
    import json

    from app.models import GlobalExclusion
    from app.services.exclusions import exclusion_id, normalized_pair

    entry = ensure_entry(db, "Artist", "Hidden")
    youtube.queue(db, entry.id, entry.artist, entry.title)
    db.commit()
    url = f"/api/catalog/{entry.id}/youtube"
    hidden = json.dumps([{"artist": "Artist", "title": "Hidden"}])
    for path in (url, url + "/state"):
        assert client.get(path, params={"exclusions": hidden}, headers=AUTH).status_code == 404
    assert (
        client.post(
            url + "/search", params={"exclusions": hidden}, json={}, headers=AUTH
        ).status_code
        == 404
    )
    data = client.get("/api/admin/workers", params={"exclusions": hidden}, headers=AUTH).json()
    assert data["youtube"]["pending"] == 0
    artist, title = normalized_pair(entry.artist, entry.title)
    db.add(
        GlobalExclusion(
            id=exclusion_id(entry.artist, entry.title),
            artist=entry.artist,
            title=entry.title,
            artist_normalized=artist,
            title_normalized=title,
        )
    )
    db.commit()
    for path in (url, url + "/state"):
        assert client.get(path, headers=AUTH).status_code == 404
    assert client.post(url + "/search", json={}, headers=AUTH).status_code == 404
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    assert asyncio.run(youtube.worker_round(factory)) is False
    assert not remote
