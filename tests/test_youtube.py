import asyncio
import json

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.config import settings
from app.models import GlobalExclusion, TrackMetadata
from app.services import youtube
from app.services.exclusions import exclusion_id, normalized_pair
from app.services.track_identity import identity


@pytest.fixture(autouse=True)
def video_state(monkeypatch):
    youtube._cache.clear()
    youtube._requests.clear()
    monkeypatch.setattr(settings, "youtube_api_key", SecretStr("private-test-only-key"))


def test_verified_video_cache_versions_and_quota(monkeypatch):
    requests = []
    original = httpx.AsyncClient

    def response(request):
        requests.append(request)
        assert "private-test-only-key" not in str(request.url)
        assert request.headers["X-Goog-Api-Key"] == "private-test-only-key"
        if request.url.path.endswith("search"):
            return httpx.Response(200, json={"items": [{"id": {"videoId": "abcdefghijk"}}]})
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "abcdefghijk",
                        "snippet": {
                            "title": "Artist - Song (Official Video)",
                            "channelTitle": "ArtistVEVO",
                        },
                        "status": {"embeddable": True, "privacyStatus": "public"},
                    }
                ]
            },
        )

    monkeypatch.setattr(
        youtube.httpx,
        "AsyncClient",
        lambda **kw: original(**kw, transport=httpx.MockTransport(response)),
    )
    monkeypatch.setattr(settings, "youtube_searches_per_day", 1)
    result = asyncio.run(youtube.discover("Artist", "Song"))
    assert result["status"] == "matched"
    assert result["candidates"][0]["id"] == "abcdefghijk"
    assert asyncio.run(youtube.discover("ARTIST", "SONG")) == result
    assert len(requests) == 2
    with pytest.raises(HTTPException) as error:
        asyncio.run(youtube.discover("Other", "Song"))
    assert error.value.status_code == 429
    assert (
        youtube.candidate_score(
            "Artist", "Song", {"title": "Artist Song cover", "channelTitle": "Artist"}
        )
        is None
    )


def test_unknown_artist_ambiguous_regions_errors_and_missing_configuration(monkeypatch):
    original = httpx.AsyncClient
    mode = ["ambiguous"]

    def response(request):
        if mode[0] == "error":
            return httpx.Response(403, json={"error": {"message": "private-test-only-key"}})
        if request.url.path.endswith("search"):
            return httpx.Response(
                200,
                json={"items": [{"id": {"videoId": v}} for v in ("abcdefghijk", "ABCDEFGHIJK")]},
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": v,
                        "snippet": {"title": "Artist Song", "channelTitle": "Artist"},
                        "status": {"embeddable": True, "privacyStatus": "public"},
                        "contentDetails": {
                            "regionRestriction": {"blocked": ["FR"] if mode[0] == "blocked" else []}
                        },
                    }
                    for v in ("abcdefghijk", "ABCDEFGHIJK")
                ]
            },
        )

    monkeypatch.setattr(
        youtube.httpx,
        "AsyncClient",
        lambda **kw: original(**kw, transport=httpx.MockTransport(response)),
    )
    assert asyncio.run(youtube.discover("Artist", "Song"))["status"] == "ambiguous"
    youtube._cache.clear()
    mode[0] = "blocked"
    assert asyncio.run(youtube.discover("Artist", "Song"))["status"] == "not_found"
    youtube._cache.clear()
    mode[0] = "error"
    with pytest.raises(HTTPException) as error:
        asyncio.run(youtube.discover("Artist", "Song"))
    assert error.value.status_code == 503 and "private-test-only-key" not in error.value.detail
    monkeypatch.setattr(settings, "youtube_api_key", SecretStr(""))
    with pytest.raises(HTTPException) as error:
        asyncio.run(youtube.discover("Artist", "Song"))
    assert error.value.status_code == 503


def test_artist_channel_outranks_official_claim_and_different_versions_are_rejected():
    official = youtube.candidate_score(
        "Artist", "Song", {"title": "Artist Song", "channelTitle": "Artist - Topic"}
    )
    claimed = youtube.candidate_score(
        "Artist", "Song", {"title": "Artist Song Official Video", "channelTitle": "Uploader"}
    )
    assert official > claimed
    for variant in ("rehearsal", "loop", "slowed", "concert"):
        assert (
            youtube.candidate_score(
                "Artist", "Song", {"title": f"Artist Song {variant}", "channelTitle": "Artist"}
            )
            is None
        )


def test_endpoint_respects_exclusions_before_external_search(db, client, monkeypatch):
    key = identity("Artist", "Song")
    db.add(TrackMetadata(id=key, artist="Artist", title="Song"))
    db.commit()
    calls = []

    async def discover(artist, title):
        calls.append((artist, title))
        return {"status": "not_found", "candidates": []}

    monkeypatch.setattr(youtube, "discover", discover)
    assert client.get(f"/api/catalog/{key}/youtube").status_code == 200
    assert calls == [("Artist", "Song")]
    hidden = json.dumps([{"artist": "Artist", "title": "Song"}])
    assert (
        client.get(f"/api/catalog/{key}/youtube", params={"exclusions": hidden}).status_code == 404
    )
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
    assert client.get(f"/api/catalog/{key}/youtube").status_code == 404
    assert len(calls) == 1
    assert client.get("/api/catalog/unknown/youtube").status_code == 404
