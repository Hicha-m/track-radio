import asyncio

import httpx
import pytest
from pydantic import SecretStr

from app.config import settings
from app.services import spotify


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setattr(settings, "spotify_client_id", SecretStr("test-client"))
    monkeypatch.setattr(settings, "spotify_client_secret", SecretStr("test-secret"))
    monkeypatch.setattr(spotify, "_token", None)
    monkeypatch.setattr(spotify, "_token_until", 0)
    monkeypatch.setattr(spotify, "_token_identity", None)


def test_spotify_caches_token_and_maps_recording_without_inventing_preview():
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.host == "accounts.spotify.com":
            assert request.method == "POST"
            assert b"grant_type=client_credentials" in request.content
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "server-only", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer server-only"
        assert request.url.params["limit"] == "10"
        return httpx.Response(
            200,
            json={
                "tracks": {
                    "items": [
                        {
                            "id": "42",
                            "name": "Soon",
                            "artists": [{"name": "Mosimann"}],
                            "duration_ms": 151000,
                            "album": {
                                "name": "Soon",
                                "images": [{"url": "https://img.example/cover"}],
                            },
                            "external_ids": {"isrc": "FR1234567890"},
                            "external_urls": {"spotify": "https://open.spotify.com/track/42"},
                        }
                    ]
                }
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            first = await spotify.search_spotify(client, "Mosimann", "Soon")
            await spotify.search_spotify(client, "Mosimann", "Soon")
            return first

    [result] = asyncio.run(run())
    assert len(calls) == 3
    assert result["isrc"] == "FR1234567890"
    assert result["duration"] == 151
    assert result["preview_url"] is None
    assert "server-only" not in str(result)
    assert "test-secret" not in repr(settings)


def test_unauthorized_search_refreshes_access_token_once():
    tokens = 0

    def respond(request):
        nonlocal tokens
        if request.url.host == "accounts.spotify.com":
            tokens += 1
            return httpx.Response(200, json={"access_token": f"token-{tokens}", "expires_in": 3600})
        if tokens == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"tracks": {"items": []}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            assert await spotify.search_spotify(client, "Mosimann", "Soon") == []

    asyncio.run(run())
    assert tokens == 2


def test_forbidden_search_is_not_retried_or_treated_as_missing_song():
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.host == "accounts.spotify.com":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        return httpx.Response(403)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await spotify.search_spotify(client, "Mosimann", "Soon")

    asyncio.run(run())
    assert len(calls) == 2


def test_forbidden_provider_search_keeps_existing_metadata(db, client, monkeypatch):
    from app.services.catalog import ensure_entry

    entry = ensure_entry(db, "Mosimann", "Soon")
    entry.selected = {"provider": "deezer", "provider_id": "42"}
    db.commit()

    async def forbidden(*args):
        request = httpx.Request("GET", "https://api.spotify.com/v1/search")
        response = httpx.Response(403, request=request)
        response.raise_for_status()

    monkeypatch.setattr("app.catalog_api.search_provider", forbidden)
    response = client.post(
        f"/api/catalog/{entry.id}/search",
        json={"provider": "spotify", "artist": "Mosimann", "title": "Soon"},
        headers={"Authorization": "Bearer test-admin-token"},
    )
    assert response.status_code == 502
    assert "Spotify refuse" in response.json()["detail"]
    db.refresh(entry)
    assert entry.selected["provider"] == "deezer"
    assert entry.manual is False


def test_first_result_is_cached_and_never_saved_as_a_validated_recording(monkeypatch):
    monkeypatch.setattr(spotify, "_first_cache", {})
    monkeypatch.setattr(spotify, "_unavailable_until", 0)
    calls = []
    first = {
        "title": "Uber",
        "artist": "Franglish, KeBlack, Mauvais djo",
        "url": "https://open.spotify.com/track/7DstNl9Mumkt3ujs7V84qq",
    }

    async def search(client, artist, title):
        calls.append((artist, title))
        return [first, {"title": "Autre version"}]

    monkeypatch.setattr(spotify, "search_spotify", search)

    async def run():
        for _ in range(2):
            result = await spotify.first_result("KeBlack, Franglish, Mauvais Djo", "Uber")
            assert result == {"status": "found", "track": first}

    asyncio.run(run())
    assert len(calls) == 1


def test_public_resolution_preserves_search_on_provider_failure(client, db, monkeypatch):
    from app.services.catalog import ensure_entry

    monkeypatch.setattr(spotify, "_first_cache", {})
    monkeypatch.setattr(spotify, "_unavailable_until", 0)
    calls = []

    async def denied(client, artist, title):
        calls.append(title)
        raise httpx.HTTPStatusError(
            "Forbidden",
            request=httpx.Request("GET", "https://api.spotify.com/v1/search"),
            response=httpx.Response(403),
        )

    monkeypatch.setattr(spotify, "search_spotify", denied)
    entry = ensure_entry(db, "KeBlack, Franglish, Mauvais Djo", "Uber")
    db.commit()
    for _ in range(2):
        response = client.get(
            "/api/catalog/spotify-match",
            params={
                "artist": entry.artist,
                "title": entry.title,
            },
        )
        assert response.json() == {"status": "unavailable", "track": None}
    assert len(calls) == 1
    assert entry.selected is None
    assert not entry.manual
    assert client.get("/api/catalog/spotify-match?title=unknown").json()["status"] == "pending"
