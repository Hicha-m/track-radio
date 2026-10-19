import asyncio
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import httpx

from app.services import catalog


def snapshot():
    return {
        "artist": "BEN PLG",
        "title": "Histoire sans fin (feat Mathilde)",
        "selected": None,
        "candidates": [],
        "lyrics": None,
        "lyrics_candidates": [],
        "lyrics_status": "pending",
        "status": "pending",
        "attempts": {},
    }


def matching(provider="itunes"):
    return {
        "provider": provider,
        "provider_id": "42",
        "artist": "BEN PLG",
        "title": "Histoire sans fin (feat. Mathilde)",
        "duration": 200,
        "genres": ["Hip-Hop/Rap"],
        "genre_source": provider,
    }


def test_wrong_mathilde_tracks_fall_back_to_itunes_and_lyrics_separate(monkeypatch):
    calls = []

    async def search(client, provider, artist, title):
        calls.append(provider)
        return (
            [{**matching("deezer"), "artist": "Mathilde", "title": "Révolution"}]
            if provider == "deezer"
            else [matching()]
        )

    async def lyrics(client, artist, title):
        calls.append("lrclib")
        return []

    monkeypatch.setattr(catalog, "search_provider", search)
    monkeypatch.setattr(catalog, "search_lyrics", lyrics)
    result = asyncio.run(catalog.enrich_progress(None, snapshot()))
    assert calls == ["deezer", "itunes", "lrclib"]
    assert result["selected"]["artist"] == "BEN PLG"
    assert result["status"] == "matched"
    assert result["lyrics_status"] == "not_found"
    assert result["automatic_done"]
    assert result["attempts"]["deezer"]["status"] == "not_found"
    assert result["attempts"]["deezer"]["candidates"][0]["artist"] == "Mathilde"
    assert not result["lyrics_candidates"]


def test_errors_retry_only_unresolved_provider_and_final_negatives_stop(monkeypatch):
    calls = []

    async def search(client, provider, *args):
        calls.append(provider)
        if provider == "itunes" and calls.count(provider) == 1:
            raise httpx.ReadTimeout("temporary")
        return []

    monkeypatch.setattr(catalog, "search_provider", search)
    result = asyncio.run(catalog.enrich_progress(None, snapshot()))
    assert not result["automatic_done"] and result["status"] == "error"
    result = asyncio.run(catalog.enrich_progress(None, result))
    assert calls == ["deezer", "itunes", "itunes"]
    assert result["automatic_done"] and result["status"] == "not_found"


def test_ambiguous_results_try_next_provider_without_guessing(monkeypatch):
    async def search(client, provider, *args):
        return [matching(provider), {**matching(provider), "provider_id": "43"}]

    monkeypatch.setattr(catalog, "search_provider", search)
    result = asyncio.run(catalog.enrich_progress(None, snapshot()))
    assert result["selected"] is None
    assert result["status"] == "ambiguous" and result["automatic_done"]
    assert len(result["candidates"]) == 4


def test_temporary_lyrics_failure_retries_no_recording_search(monkeypatch):
    calls = []

    async def search(client, provider, *args):
        calls.append(provider)
        return [] if provider == "deezer" else [matching()]

    async def lyrics(client, *args):
        calls.append("lrclib")
        if calls.count("lrclib") == 1:
            raise httpx.ReadTimeout("temporary")
        return [{**matching(), "id": "7", "plain": "Words", "provider": "lrclib"}]

    monkeypatch.setattr(catalog, "search_provider", search)
    monkeypatch.setattr(catalog, "search_lyrics", lyrics)
    result = asyncio.run(catalog.enrich_progress(None, snapshot()))
    assert result["lyrics_status"] == "error" and result["status"] == "matched"
    assert not result["automatic_done"]
    result = asyncio.run(catalog.enrich_progress(None, result))
    assert result["lyrics"]["plain"] == "Words"
    assert result["lyrics_status"] == "matched" and result["automatic_done"]
    assert calls == ["deezer", "itunes", "lrclib", "lrclib"]


def test_album_genres_cached_and_optional_errors_preserve_recording(monkeypatch):
    catalog._album_cache.clear()
    calls = []

    async def fetch(client, url):
        calls.append(url)
        return (
            {"album": {"id": 123}, "isrc": "TEST"}
            if "/track/" in url
            else {"genres": {"data": [{"name": "Rap"}]}}
        )

    monkeypatch.setattr(catalog, "fetch_json", fetch)
    for _ in range(2):
        result = asyncio.run(catalog.recording_details(None, matching("deezer")))
        assert result["genres"] == ["Rap"]
        assert result["genre_source"] == "deezer"
    assert sum("/album/" in url for url in calls) == 1


def test_worker_atomic_revision_preserves_correction_during_network_io(db, monkeypatch):
    entry = catalog.ensure_entry(db, "Artist", "Song")
    db.commit()

    @contextmanager
    def sessions():
        yield db

    async def enrich(client, previous):
        entry.selected = {"provider": "itunes", "provider_id": "manual"}
        entry.manual = True
        entry.revision += 1
        entry.status = "matched"
        db.commit()
        return {**previous, "selected": matching(), "status": "matched", "automatic_done": True}

    monkeypatch.setattr(catalog, "enrich_progress", enrich)
    asyncio.run(catalog.enrichment_round(None, sessions))
    db.refresh(entry)
    assert entry.manual and entry.selected["provider_id"] == "manual"


def test_worker_final_stop_survives_time_and_relaunch(db, client, monkeypatch):
    entry = catalog.ensure_entry(db, "Artist", "Song")
    db.commit()
    calls = []

    @contextmanager
    def sessions():
        yield db

    async def enrich(client, previous):
        calls.append(previous["title"])
        return {**deepcopy(previous), "status": "not_found", "automatic_done": True}

    monkeypatch.setattr(catalog, "enrich_progress", enrich)
    asyncio.run(catalog.enrichment_round(None, sessions))
    entry.retry_at = datetime.now(UTC) - timedelta(days=60)
    db.commit()
    asyncio.run(catalog.enrichment_round(None, sessions))
    assert len(calls) == 1
    response = client.post(
        f"/api/catalog/{entry.id}/correction",
        json={"automatic": True},
        headers={"Authorization": "Bearer test-admin-token"},
    )
    assert response.status_code == 200
    asyncio.run(catalog.enrichment_round(None, sessions))
    assert len(calls) == 2


def test_existing_recording_gains_genres_without_researching_or_losing_lyrics(monkeypatch):
    calls = []
    previous = snapshot()
    previous.update(
        selected={k: v for k, v in matching().items() if k not in {"genres", "genre_source"}},
        status="matched",
        lyrics_status="matched",
        lyrics={"plain": "Preserved"},
    )

    async def fetch(client, url, params):
        calls.append((url, params))
        return {"results": [{"trackId": 42, "primaryGenreName": "Rap"}]}

    monkeypatch.setattr(catalog, "fetch_json", fetch)
    result = asyncio.run(catalog.enrich_progress(None, previous))
    assert len(calls) == 1 and calls[0][0].endswith("/lookup")
    assert result["selected"]["genres"] == ["Rap"]
    assert result["lyrics"]["plain"] == "Preserved"
    assert result["automatic_done"]


def test_album_failure_keeps_recording_then_retries_only_album(monkeypatch):
    catalog._album_cache.clear()
    calls = []

    async def search(client, provider, *args):
        calls.append("search")
        return [
            {k: v for k, v in matching("deezer").items() if k not in {"genres", "genre_source"}}
        ]

    async def fetch(client, url):
        calls.append(url)
        if "/track/" in url:
            return {"album": {"id": 789}, "isrc": "TEST"}
        if calls.count(url) == 1:
            raise httpx.ReadTimeout("temporary")
        return {"genres": {"data": [{"name": "Rap"}]}}

    async def lyrics(*args):
        calls.append("lyrics")
        return []

    monkeypatch.setattr(catalog, "fetch_json", fetch)
    monkeypatch.setattr(catalog, "search_provider", search)
    monkeypatch.setattr(catalog, "search_lyrics", lyrics)
    result = asyncio.run(catalog.enrich_progress(None, snapshot()))
    assert result["status"] == "matched" and not result["automatic_done"]
    assert result["attempts"]["genres"]["status"] == "error"
    result = asyncio.run(catalog.enrich_progress(None, result))
    assert result["selected"]["genres"] == ["Rap"] and result["automatic_done"]
    assert calls.count("search") == 1
    assert calls.count("lyrics") == 1
    assert sum("/track/" in c for c in calls) == 1
    assert sum("/album/" in c for c in calls) == 2


def test_lyrics_missing_feature_suffix_duration_and_guest_validation(monkeypatch):
    selected = matching()
    base = {
        "id": "7",
        "artist": "BEN PLG",
        "title": "Histoire sans fin",
        "duration": 200,
        "plain": "Words",
    }
    assert catalog.lyrics_identity_match(selected, base)
    assert not catalog.lyrics_identity_match(
        selected, {**base, "title": "Histoire sans fin (feat Other)"}
    )
    assert not catalog.lyrics_identity_match(
        selected, {**base, "title": "Histoire sans fin (Live)"}
    )
    assert not catalog.lyrics_identity_match(selected, {**base, "artist": "Mathilde"})

    async def lyrics(*args):
        return [{**base, "duration": 250}]

    monkeypatch.setattr(catalog, "search_lyrics", lyrics)
    _, chosen, status = asyncio.run(catalog.lyrics_result(None, selected))
    assert chosen is None and status == "ambiguous"


def test_deezer_details_keep_only_supplied_release_and_contributors(monkeypatch):
    async def fetch(client, url):
        return {
            "release_date": "2026-01-02",
            "contributors": [{"name": "BEN PLG"}, {"name": "Mathilde"}, {"name": ""}],
            "isrc": "TEST123",
        }

    monkeypatch.setattr(catalog, "fetch_json", fetch)
    result = asyncio.run(catalog.recording_details(None, matching("deezer")))
    assert result["release_date"] == "2026-01-02"
    assert result["featured_artists"] == ["Mathilde"]
    assert result["isrc"] == "TEST123"
