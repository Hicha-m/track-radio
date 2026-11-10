import asyncio
from contextlib import contextmanager
from copy import deepcopy

import httpx
import pytest
from pydantic import SecretStr

from app.services import catalog
from app.services.temporary_artwork import artwork_confidence, catalog_artwork, choose_artwork


def candidate(**changes):
    return {
        "artist": "Phil Collins",
        "title": "You Can't Hurry Love",
        "album": "Hello, I Must Be Going!",
        "provider": "itunes",
        "provider_id": "42",
        "url": "https://music.apple.com/track/42",
        "cover_url": "https://example.test/cover.jpg",
        **changes,
    }


def test_score_retains_versions_guests_and_artist_identity():
    for artist, title, expected in [
        ("PHIL COLLINS", "You Can’t Hurry Love", 100),
        ("The Supremes", "You Can't Hurry Love", 55),
        ("Phil Collins", "You Can't Hurry Love (Live)", 55),
        ("Phil Collins feat Guest", "You Can't Hurry Love", 55),
        ("", "You Can't Hurry Love", 55),
        ("Phil Collins", "Unknown", 55),
    ]:
        assert artwork_confidence(artist, title, candidate()) == expected
    for change in [
        {"album": ""},
        {"url": "javascript:invalid"},
        {"cover_url": "http://example.test/a"},
        {"provider_id": ""},
    ]:
        assert artwork_confidence("Phil Collins", "You Can't Hurry Love", candidate(**change)) == 90


def test_no_first_image_guess_when_albums_conflict():
    artist, title = "Phil Collins", "You Can't Hurry Love"
    wrong = candidate(artist="The Supremes")
    good = candidate()
    chosen = choose_artwork(artist, title, [wrong, good])
    assert chosen["cover_url"] == good["cover_url"] and chosen["score"] == 100
    assert choose_artwork(artist, title, [wrong])["cover_url"] is None
    assert choose_artwork(artist, title, [])["status"] == "not_found"
    assert choose_artwork(artist, title, [good, candidate(provider_id="43")])["status"] == "matched"
    conflict = candidate(album="Compilation", cover_url="https://example.test/other.jpg")
    assert choose_artwork(artist, title, [good, conflict])["status"] == "ambiguous"


def test_spotify_optional_fallback_errors_and_no_request_when_local_evidence(monkeypatch):
    from app.config import settings

    calls = []

    async def search(client, provider, *args):
        calls.append(provider)
        return [candidate(provider="spotify")]

    monkeypatch.setattr(catalog, "search_provider", search)
    monkeypatch.setattr(settings, "spotify_client_id", SecretStr(""))
    monkeypatch.setattr(settings, "spotify_client_secret", SecretStr(""))
    assert (
        asyncio.run(catalog_artwork(None, "Phil Collins", "You Can't Hurry Love", []))["spotify"]
        == "not_configured"
    )
    assert not calls
    monkeypatch.setattr(settings, "spotify_client_id", SecretStr("fixture"))
    monkeypatch.setattr(settings, "spotify_client_secret", SecretStr("fixture"))
    assert (
        asyncio.run(catalog_artwork(None, "Phil Collins", "You Can't Hurry Love", []))["status"]
        == "matched"
    )
    assert calls == ["spotify"]
    asyncio.run(catalog_artwork(None, "Phil Collins", "You Can't Hurry Love", [candidate()]))
    assert calls == ["spotify"]

    async def unavailable(*args):
        raise httpx.ReadTimeout("fixture")

    monkeypatch.setattr(catalog, "search_provider", unavailable)
    assert (
        asyncio.run(catalog_artwork(None, "Phil Collins", "You Can't Hurry Love", []))["status"]
        == "error"
    )


def test_background_persists_cover_without_selecting_a_recording(db, client, monkeypatch):
    from tests.test_enrichment_progress import snapshot

    # Two catalogue entries sharing artwork cannot identify a unique recording.
    async def search(_client, provider, artist, title):
        return [candidate(provider=provider), candidate(provider=provider, provider_id="43")]

    monkeypatch.setattr(catalog, "search_provider", search)
    previous = snapshot()
    previous.update(artist="Phil Collins", title="You Can't Hurry Love")
    result = asyncio.run(catalog.enrich_progress(None, previous))
    assert result["selected"] is None and result["status"] == "ambiguous"
    assert result["temporary_cover_url"] == candidate()["cover_url"]
    assert result["attempts"]["artwork"]["score"] == 100
    entry = catalog.ensure_entry(db, previous["artist"], previous["title"])
    db.commit()

    @contextmanager
    def sessions():
        yield db

    async def enrich(_client, _snapshot):
        return {**deepcopy(_snapshot), **result}

    monkeypatch.setattr(catalog, "enrich_progress", enrich)
    asyncio.run(catalog.enrichment_round(None, sessions))
    db.refresh(entry)
    assert entry.selected is None and entry.temporary_cover_url == candidate()["cover_url"]
    response = client.get(
        "/api/catalog", params={"artist": previous["artist"], "title": previous["title"]}
    )
    assert response.json()["artwork"]["score"] == 100
    # A later validated cover takes precedence, without changing historical plays.
    entry.selected = candidate(cover_url="https://example.test/validated.jpg")
    db.commit()
    response = client.get(
        "/api/catalog", params={"artist": previous["artist"], "title": previous["title"]}
    )
    assert response.json()["cover_url"].endswith("/validated.jpg")
    assert "artwork" not in response.json()


def test_legacy_final_entry_gets_artwork_once_without_reopening_recording(db, monkeypatch):

    entry = catalog.ensure_entry(db, "Phil Collins", "You Can't Hurry Love")
    entry.status = "ambiguous"
    entry.automatic_done = True
    entry.attempts = {
        provider: {"status": "ambiguous", "candidates": [candidate(), candidate(provider_id="43")]}
        for provider in ("deezer", "itunes")
    }
    entry.candidates = [candidate(), candidate(provider_id="43")]
    db.commit()

    @contextmanager
    def sessions():
        yield db

    async def forbidden(*args):
        raise AssertionError("Final recording searches must not be restarted")

    monkeypatch.setattr(catalog, "search_provider", forbidden)
    catalog.ensure_entry(db, entry.artist, entry.title)
    db.commit()
    asyncio.run(catalog.enrichment_round(None, sessions))
    db.refresh(entry)
    assert entry.temporary_cover_url == candidate()["cover_url"]
    assert entry.selected is None and entry.automatic_done
    revision = entry.revision
    asyncio.run(catalog.enrichment_round(None, sessions))
    db.refresh(entry)
    assert entry.revision == revision


def test_official_station_artwork_arriving_during_lookup_is_preserved(db, monkeypatch):
    entry = catalog.ensure_entry(db, "Phil Collins", "You Can't Hurry Love")
    db.commit()

    @contextmanager
    def sessions():
        yield db

    async def enrich(_client, previous):
        entry.temporary_cover_url = "https://example.test/station.jpg"
        db.commit()
        return {**previous, "temporary_cover_url": candidate()["cover_url"], "automatic_done": True}

    monkeypatch.setattr(catalog, "enrich_progress", enrich)
    asyncio.run(catalog.enrichment_round(None, sessions))
    db.refresh(entry)
    assert entry.temporary_cover_url.endswith("/station.jpg")


@pytest.mark.parametrize("admin", [False, True])
def test_queued_artwork_respects_global_and_personal_visibility(db, client, monkeypatch, admin):
    from app.models import GlobalExclusion
    from app.services.exclusions import exclusion_id, normalized_pair

    entry = catalog.ensure_entry(db, "Phil Collins", "You Can't Hurry Love")
    entry.temporary_cover_url = candidate()["cover_url"]
    db.commit()
    import json

    headers = {"Authorization": "Bearer test-admin-token"} if admin else {}
    response = client.get(
        "/api/catalog",
        headers=headers,
        params={
            "artist": entry.artist,
            "title": entry.title,
            "exclusions": json.dumps([{"artist": entry.artist, "title": entry.title}]),
        },
    )
    assert response.status_code == 404
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

    @contextmanager
    def sessions():
        yield db

    async def forbidden(*args):
        raise AssertionError("Excluded track must not trigger artwork or recording lookup")

    monkeypatch.setattr(catalog, "enrich_progress", forbidden)
    asyncio.run(catalog.enrichment_round(None, sessions))
    assert (
        client.get(
            "/api/catalog", params={"artist": entry.artist, "title": entry.title}, headers=headers
        ).status_code
        == 404
    )
    assert db.get(type(entry), entry.id) is not None
