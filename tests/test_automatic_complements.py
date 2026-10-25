import asyncio
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from app.config import settings
from app.models import GlobalExclusion
from app.services import catalog, catalog_completion, providers
from app.services.exclusions import exclusion_id, normalized_pair

AUTH = {"Authorization": "Bearer test-admin-token"}


def recording(provider="deezer", **changes):
    return {
        "provider": provider,
        "provider_id": "42",
        "artist": "Artist",
        "title": "Song",
        "album": "Album",
        "duration": 180,
        "genres": ["Pop"],
        "isrc": "FR123",
        "url": f"https://example.test/{provider}/42",
        **changes,
    }


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    for key in ("spotify_client_id", "spotify_client_secret", "lastfm_api_key"):
        monkeypatch.setattr(settings, key, SecretStr(""))


def fake_providers(monkeypatch, result):
    calls = []

    async def search(client, provider, artist, title, **kwargs):
        calls.append((provider, artist, title))
        value = result(provider)
        if isinstance(value, Exception):
            raise value
        return value

    async def page(client, provider, artist, title, **kwargs):
        return {"candidates": await search(client, provider, artist, title)}

    async def details(client, candidate):
        return deepcopy(candidate)

    monkeypatch.setattr(catalog_completion, "search_provider", search)
    monkeypatch.setattr(providers, "search_recordings", page)
    monkeypatch.setattr(catalog_completion, "recording_details", details)
    return calls


@pytest.mark.parametrize("primary", ["deezer", "itunes", "musicbrainz", "spotify", "lastfm"])
def test_complements_visit_other_available_providers_without_replacing_primary(
    monkeypatch, primary
):
    for key in ("spotify_client_id", "spotify_client_secret", "lastfm_api_key"):
        monkeypatch.setattr(settings, key, SecretStr("test-only"))
    before = recording(primary, genres=["Chosen genre"])
    calls = fake_providers(
        monkeypatch,
        lambda p: [
            recording(
                p,
                genres=["Other genre"],
                preview_url=f"https://example.test/{p}/preview.mp3",
                cover_url=f"https://example.test/{p}/cover.jpg",
                release_date="2026-01-01",
            )
        ],
    )
    result, progress = asyncio.run(catalog_completion.complete_across_providers(None, before))
    assert {c[0] for c in calls} == {"deezer", "itunes", "musicbrainz", "spotify", "lastfm"} - {
        primary
    }
    assert all((a, t) == ("Artist", "Song") for _, a, t in calls)
    for key, value in before.items():
        assert result[key] == value
    assert result["preview_url"] and result["cover_url"] and result["release_date"]
    assert result["preview_source"]["provider"] != primary
    assert result["field_sources"]["cover_url"] != primary
    assert progress["status"] == "done"
    assert before == recording(primary, genres=["Chosen genre"])


@pytest.mark.parametrize(
    "changes",
    [
        {"artist": "Another"},
        {"title": "Song (Live)"},
        {"title": "Song (feat. Other)"},
        {"duration": 190},
        {"isrc": "DIFFERENT"},
        {"duration": None, "isrc": None},
    ],
)
def test_relaunch_rejects_incompatible_recordings(monkeypatch, changes):
    before = recording()
    fake_providers(
        monkeypatch, lambda p: [recording(p, cover_url="https://example.test/cover", **changes)]
    )
    result, progress = asyncio.run(catalog_completion.complete_across_providers(None, before))
    assert result == before
    assert all(progress["providers"][p]["status"] == "not_found" for p in ("itunes", "musicbrainz"))


def test_ambiguity_and_other_edition_do_not_supply_cover_or_date(monkeypatch):
    calls = fake_providers(
        monkeypatch,
        lambda p: (
            [recording(p), recording(p, provider_id="99")]
            if p == "itunes"
            else [
                recording(
                    p,
                    album="Reissue",
                    release_date="2026-01-01",
                    cover_url="https://example.test/wrong.jpg",
                )
            ]
        ),
    )
    result, progress = asyncio.run(catalog_completion.complete_across_providers(None, recording()))
    assert not result.get("cover_url") and not result.get("release_date")
    assert result["album"] == "Album" and len(calls) == 2
    assert progress["providers"]["itunes"]["status"] == "ambiguous"


def test_errors_retry_only_failed_provider_and_missing_credentials_are_skipped(monkeypatch):
    failed = True

    def results(provider):
        return (
            httpx.ReadTimeout("temporary")
            if provider == "itunes" and failed
            else [recording(provider, preview_url=f"https://example.test/{provider}/preview.mp3")]
        )

    calls = fake_providers(monkeypatch, results)
    result, progress = asyncio.run(catalog_completion.complete_across_providers(None, recording()))
    assert progress["status"] == "error" and result["preview_source"]["provider"] == "musicbrainz"
    assert progress["providers"]["spotify"]["status"] == "not_configured"
    failed = False
    result, progress = asyncio.run(
        catalog_completion.complete_across_providers(None, result, progress)
    )
    assert [c[0] for c in calls] == ["itunes", "musicbrainz", "itunes"]
    assert progress["status"] == "done"
    assert result["preview_source"]["provider"] == "musicbrainz"


def test_cover_archive_failure_keeps_other_additions_and_can_be_retried(monkeypatch):
    fake_providers(
        monkeypatch,
        lambda p: (
            [] if p == "itunes" else [recording(p, release_id="release", release_date="2026-01-01")]
        ),
    )
    failed = True

    async def artwork(*args):
        return {
            "status": "error" if failed else "matched",
            "cover_url": None if failed else "https://example.test/art.jpg",
        }

    monkeypatch.setattr(providers, "cover_art", artwork)
    result, progress = asyncio.run(catalog_completion.complete_across_providers(None, recording()))
    assert result["release_date"] == "2026-01-01" and progress["status"] == "error"
    failed = False
    result, progress = asyncio.run(
        catalog_completion.complete_across_providers(None, result, progress)
    )
    assert result["cover_url"] == "https://example.test/art.jpg" and progress["status"] == "done"
    assert progress["providers"]["musicbrainz"]["filled"] == [
        "additional_sources",
        "cover_url",
        "release_date",
    ]


def test_permission_denial_is_not_retried_and_does_not_block_other_providers(monkeypatch):
    denied = httpx.HTTPStatusError(
        "denied",
        request=httpx.Request("GET", "https://example.test/search"),
        response=httpx.Response(403),
    )
    calls = fake_providers(monkeypatch, lambda p: denied if p == "itunes" else [recording(p)])
    result, progress = asyncio.run(catalog_completion.complete_across_providers(None, recording()))
    assert progress["status"] == "done"
    assert progress["providers"]["itunes"]["status"] == "unavailable"
    assert progress["providers"]["musicbrainz"]["status"] == "matched"
    asyncio.run(catalog_completion.complete_across_providers(None, result, progress))
    assert len(calls) == 2


def test_select_then_relaunch_enriches_existing_genres_preserves_lyrics_and_stops(
    db, client, monkeypatch
):
    entry = catalog.ensure_entry(db, "Collected Artist", "Collected title")
    entry.candidates = [recording()]
    db.commit()
    path = f"/api/catalog/{entry.id}/correction"
    assert client.post(path, json={"candidate_id": "deezer:42"}, headers=AUTH).status_code == 200
    db.refresh(entry)
    entry.lyrics = {"plain": "Keep these lyrics"}
    entry.lyrics_status = "matched"
    db.commit()
    original = deepcopy(entry.selected)
    assert client.post(path, json={"automatic": True}).status_code == 401
    response = client.post(path, json={"automatic": True}, headers=AUTH)
    assert response.json()["relaunch"] == "complement"
    db.refresh(entry)
    assert entry.attempts["complements"]["status"] == "pending"
    assert entry.selected == original and not entry.automatic_done
    calls = fake_providers(
        monkeypatch, lambda p: [recording(p, cover_url="https://example.test/art.jpg")]
    )

    @contextmanager
    def sessions():
        yield db

    asyncio.run(catalog.enrichment_round(None, sessions))
    db.refresh(entry)
    for key, value in original.items():
        assert entry.selected[key] == value
    assert entry.selected["cover_url"] == "https://example.test/art.jpg"
    assert entry.lyrics == {"plain": "Keep these lyrics"}
    assert entry.automatic_done and entry.primary_genre == "Pop"
    assert all(c[1:] == ("Artist", "Song") for c in calls)
    asyncio.run(catalog.enrichment_round(None, sessions))
    assert len(calls) == 2
    client.post(path, json={"automatic": True}, headers=AUTH)
    asyncio.run(catalog.enrichment_round(None, sessions))
    assert len(calls) == 4


@pytest.mark.parametrize("during_io", [False, True])
def test_queued_relaunch_respects_blacklist_and_keeps_history(db, monkeypatch, during_io):
    entry = catalog.ensure_entry(db, "Artist", "Song")
    entry.selected = recording()
    entry.status = "matched"
    entry.lyrics_status = "not_found"
    entry.attempts = {"complements": {"status": "pending", "providers": {}}}
    db.commit()

    def exclude():
        artist, title = normalized_pair("Artist", "Song")
        db.add(
            GlobalExclusion(
                id=exclusion_id("Artist", "Song"),
                artist="Artist",
                title="Song",
                artist_normalized=artist,
                title_normalized=title,
            )
        )
        db.commit()

    calls = []

    async def enrich(client, snapshot):
        calls.append(1)
        exclude()
        return {
            **snapshot,
            "selected": {**snapshot["selected"], "cover_url": "https://example.test/art"},
            "automatic_done": True,
        }

    monkeypatch.setattr(catalog, "enrich_progress", enrich)

    @contextmanager
    def sessions():
        yield db

    if not during_io:
        exclude()
    asyncio.run(catalog.enrichment_round(None, sessions))
    db.refresh(entry)
    assert len(calls) == int(during_io)
    assert entry.selected == recording() and db.get(type(entry), entry.id) is not None


def test_relaunch_retry_and_concurrent_manual_choice(db, client, monkeypatch):
    entry = catalog.ensure_entry(db, "Artist", "Song")
    entry.selected = recording()
    entry.status = "matched"
    entry.lyrics_status = "not_found"
    entry.attempts = {"complements": {"status": "pending", "providers": {}}}
    db.commit()
    calls = fake_providers(monkeypatch, lambda p: httpx.ReadTimeout("temporary"))

    @contextmanager
    def sessions():
        yield db

    asyncio.run(catalog.enrichment_round(None, sessions))
    db.refresh(entry)
    assert not entry.automatic_done and entry.retry_at is not None
    assert entry.selected == recording()
    entry.retry_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    async def corrected_during_search(client, snapshot):
        entry.candidates = [recording("itunes", provider_id="new-choice")]
        db.commit()
        client_response = client_api.post(
            f"/api/catalog/{entry.id}/correction",
            json={"candidate_id": "itunes:new-choice"},
            headers=AUTH,
        )
        assert client_response.status_code == 200
        return {**snapshot, "automatic_done": True}

    client_api = client
    monkeypatch.setattr(catalog, "enrich_progress", corrected_during_search)
    asyncio.run(catalog.enrichment_round(None, sessions))
    db.refresh(entry)
    assert entry.manual and entry.selected["provider_id"] == "new-choice"
    assert len(calls) == 2
