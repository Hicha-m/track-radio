import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.services.catalog import (
    enrich_entry,
    enrichment_round,
    ensure_entry,
    exact_match,
    fetch_json,
    identity,
    search_provider,
)


def candidate(track_id=1, title="Soon (feat. Gaëtan Roussel)", artist="Mosimann"):
    return {
        "id": track_id,
        "title": title,
        "duration": 151,
        "artist": {"name": artist},
        "album": {"title": "Soon", "cover_big": "https://cdn-images.dzcdn.net/cover.jpg"},
        "preview": "https://cdnt-preview.dzcdn.net/audio.mp3",
    }


def run_enrichment(rows):
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.path == "/search":
            return httpx.Response(200, json={"data": rows})
        if request.url.path.startswith("/track/"):
            return httpx.Response(200, json={"isrc": "FR1234567890"})
        return httpx.Response(200, json=[])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await enrich_entry(client, "MOSIMANN", "Soon (ft Gaetan Roussel)")

    return asyncio.run(run()), calls


def test_stable_identity_preserves_recording_versions():
    assert identity("MOSIMANN", " Soon ") == identity("mosimann", "soon")
    assert identity("Mosimann", "Soon") != identity("Mosimann", "Soon (Radio edit)")
    assert identity(None, "Soon") != identity("Mosimann", "Soon")


def test_match_rejects_wrong_artist_guest_and_version():
    target = {"artist": "Mosimann", "title": "Soon (feat. Gaëtan Roussel)"}
    assert exact_match("MOSIMANN", "Soon (ft Gaetan Roussel)", target)
    assert not exact_match(None, "Soon", target)
    assert not exact_match("Other", "Soon", target)
    assert not exact_match("Mosimann", "Soon (ft Another Singer)", target)
    assert not exact_match(
        "Mosimann", "Soon", {**target, "title": target["title"] + " (Radio edit)"}
    )


def test_unique_match_gets_isrc_but_not_a_similar_title():
    (_, selected, lyrics), calls = run_enrichment([candidate(), candidate(2, "Soon (Radio edit)")])
    assert selected["isrc"] == "FR1234567890"
    assert selected["provider_id"] == "1"
    assert lyrics is None
    assert len(calls) == 3


@pytest.mark.parametrize("rows", [[], [candidate(1), candidate(2)], [candidate(1, artist="Other")]])
def test_missing_or_ambiguous_results_are_not_selected(rows):
    (_, selected, _), calls = run_enrichment(rows)
    assert selected is None
    assert len(calls) == 1


def test_itunes_json_maps_units_and_missing_preview():
    async def run():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "trackId": 42,
                            "trackName": "Soon",
                            "artistName": "Mosimann",
                            "trackTimeMillis": 151000,
                            "artworkUrl100": "javascript:alert(1)",
                        }
                    ]
                },
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            return await search_provider(client, "itunes", "Mosimann", "Soon")

    [result] = asyncio.run(run())
    assert result["duration"] == 151
    assert result["preview_url"] is None
    assert result["cover_url"] is None


def test_provider_error_and_oversized_response_are_not_empty_success():
    async def run(payload):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
        ) as client:
            await fetch_json(client, "https://api.deezer.com/search")

    for payload in [b'{"error":{"code":4}}', b"x" * (512 * 1024 + 1)]:
        with pytest.raises(ValueError):
            asyncio.run(run(payload))


def test_read_metadata_never_calls_provider_and_shared_identity(db, client, monkeypatch):
    entry = ensure_entry(db, "Mosimann", "Soon")
    entry.selected = {"provider": "deezer", "provider_id": "42", "isrc": "FR1234567890"}
    entry.status = "matched"
    db.commit()
    assert ensure_entry(db, "MOSIMANN", "soon").id == entry.id
    response = client.get("/api/catalog", params={"artist": "MOSIMANN", "title": "soon"})
    assert response.json()["recording_id"] == "isrc:FR1234567890"
    assert client.get("/api/catalog", params={"title": "unknown"}).json()["status"] == "pending"


def test_corrections_require_auth_and_only_accept_stored_candidates(db, client):
    entry = ensure_entry(db, "Mosimann", "Soon")
    entry.candidates = [{"provider": "itunes", "provider_id": "42", "title": "Soon"}]
    db.commit()
    url = f"/api/catalog/{entry.id}/correction"
    assert client.post(url, json={"candidate_id": "itunes:42"}).status_code == 401
    headers = {"Authorization": "Bearer test-admin-token"}
    assert client.post(url, json={"candidate_id": "deezer:999"}, headers=headers).status_code == 422
    response = client.post(url, json={"candidate_id": "itunes:42"}, headers=headers)
    assert response.json()["recording_id"] == "itunes:42"
    assert response.json()["manual"] is True
    db.refresh(entry)
    assert entry.provider == "itunes"
    assert (
        client.post(url, json={"candidate_id": None}, headers=headers).json()["status"]
        == "rejected"
    )
    assert client.post(url, json={"automatic": True}, headers=headers).json()["manual"] is False


def test_lyrics_correction_and_authenticated_provider_switch(db, client, monkeypatch):
    entry = ensure_entry(db, "Mosimann", "Soon")
    db.commit()

    async def search(*args):
        return [{"id": "7", "plain": "Words", "synced": "", "provider": "lrclib"}]

    monkeypatch.setattr("app.catalog_api.search_lyrics", search)
    headers = {"Authorization": "Bearer test-admin-token"}
    url = f"/api/catalog/{entry.id}"
    response = client.post(
        url + "/search", json={"provider": "lrclib", "title": "Soon"}, headers=headers
    )
    assert response.status_code == 200
    response = client.post(
        url + "/correction", json={"target": "lyrics", "candidate_id": "7"}, headers=headers
    )
    assert response.json()["lyrics"]["plain"] == "Words"
    assert response.json()["manual"]


def test_negative_cache_and_manual_choice_survive_worker(db, monkeypatch):
    entry = ensure_entry(db, "Mosimann", "Soon")
    db.commit()

    @contextmanager
    def sessions():
        yield db

    calls = 0

    async def enrich(client, snapshot):
        nonlocal calls
        calls += 1
        return {**snapshot, "status": "not_found", "automatic_done": True}

    monkeypatch.setattr("app.services.catalog.enrich_progress", enrich)
    asyncio.run(enrichment_round(None, sessions))
    assert entry.status == "not_found"
    asyncio.run(enrichment_round(None, sessions))
    assert calls == 1
    entry.manual = True
    entry.status = "rejected"
    entry.retry_at = datetime.now(UTC) - timedelta(days=1)
    db.commit()
    asyncio.run(enrichment_round(None, sessions))
    assert calls == 1
    assert entry.status == "rejected"


def test_preview_uses_validated_recording_and_redirects_to_fresh_url(db, client, monkeypatch):
    from app.catalog_api import _preview_cache

    _preview_cache.clear()
    entry = ensure_entry(db, "Mosimann", "Soon")
    entry.selected = {"provider": "deezer", "provider_id": "42"}
    db.commit()

    async def fresh(*args):
        assert args[1] == "https://api.deezer.com/track/42"
        return {"preview": "https://cdnt-preview.dzcdn.net/fresh.mp3"}

    monkeypatch.setattr("app.catalog_api.fetch_json", fresh)
    response = client.get(f"/api/catalog/{entry.id}/preview", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].endswith("/fresh.mp3")
    assert response.headers["cache-control"] == "no-store"


def test_top_track_is_enriched_before_historical_entries(db, monkeypatch):
    first = ensure_entry(db, "Artist", "History A")
    second = ensure_entry(db, "Artist", "History B")
    top = ensure_entry(db, "Artist", "Popular")
    db.commit()
    calls = []

    @contextmanager
    def sessions():
        yield db

    async def enrich(client, snapshot):
        calls.append(snapshot["title"])
        return {**snapshot, "status": "not_found", "automatic_done": True}

    monkeypatch.setattr("app.services.catalog.enrich_progress", enrich)
    asyncio.run(enrichment_round(None, sessions, {top.id: 100}))
    assert calls[0] == "Popular"
    assert len(calls) == 2
    assert first.id != second.id


def test_rate_limit_pauses_further_requests_for_that_provider():
    from app.services.catalog import ProviderCooldown, _provider_pauses

    _provider_pauses.clear()
    calls = []

    def limited(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "120"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(limited)) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await fetch_json(client, "https://api.deezer.com/search")
            with pytest.raises(ProviderCooldown):
                await fetch_json(client, "https://api.deezer.com/search")

    try:
        asyncio.run(run())
        assert len(calls) == 1
    finally:
        _provider_pauses.clear()
