import asyncio

import httpx
import pytest

from app.services import catalog, providers
from app.services.catalog import ensure_entry

AUTH = {"Authorization": "Bearer test-admin-token"}
RECORDING = "01234567-1234-1234-1234-012345678901"
RELEASE = "01234567-1234-1234-1234-012345678902"


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    catalog._provider_pauses.clear()
    monkeypatch.setattr(providers, "_mb_next", 0.0)


async def run_with(handler, operation):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await operation(client)


def test_musicbrainz_search_escaping_partial_metadata_and_paging():
    def handler(request):
        assert request.url.host == "musicbrainz.org"
        assert request.url.params["query"] == 'recording:"Song \\(Live\\)" AND artist:"A \\/ B"'
        assert request.url.params["offset"] == "10"
        assert request.url.params["limit"] == "10"
        assert request.url.params["fmt"] == "json"
        assert "https://github.com/Hicha-m/track-radio" in request.headers["user-agent"]
        return httpx.Response(
            200,
            json={
                "count": 25,
                "recordings": [
                    {
                        "id": RECORDING,
                        "title": "Song (Live)",
                        "length": 192000,
                        "artist-credit": [{"name": "A", "joinphrase": " & "}, {"name": "B"}],
                        "releases": [{"id": RELEASE, "title": "Album", "date": "2026-01"}],
                        "isrcs": ["FR123"],
                        "genres": [{"name": "Hip-hop"}],
                    },
                    {
                        "id": RELEASE,
                        "title": "Incomplete",
                        "artist-credit": [{"artist": None}],
                        "length": None,
                        "isrcs": "not-an-array",
                    },
                    {"id": "invalid", "title": "Skip"},
                ],
            },
        )

    result = asyncio.run(
        run_with(
            handler,
            lambda c: providers.search_recordings(c, "musicbrainz", "A / B", "Song (Live)", 2, 10),
        )
    )
    assert result["page"] == 2 and result["has_more"]
    first, incomplete = result["candidates"]
    assert first["artist"] == "A & B" and first["duration"] == 192
    assert first["release_id"] == RELEASE and first["genres"] == ["Hip-hop"]
    assert first["isrc"] == "FR123" and first["cover_url"] is None
    assert incomplete["artist"] == "" and incomplete["duration"] is None
    assert incomplete["release_id"] is None and incomplete["isrc"] is None


def test_musicbrainz_rate_limit_serializes_concurrent_requests(monkeypatch):
    now = [0.0]
    starts = []
    monkeypatch.setattr(providers, "monotonic", lambda: now[0])

    async def pause(seconds):
        now[0] += seconds
        await real_sleep(0)

    real_sleep = asyncio.sleep
    monkeypatch.setattr(providers.asyncio, "sleep", pause)

    async def request():
        await providers.musicbrainz_slot()
        starts.append(now[0])

    async def run():
        await asyncio.gather(*(request() for _ in range(5)))

    asyncio.run(run())
    assert len(starts) == 5
    assert all(b - a >= 1 for a, b in zip(starts, starts[1:], strict=False))


@pytest.mark.parametrize("mode", ["matched", "absent", "missing", "error", "malformed"])
def test_cover_art_redirect_https_thumbnail_and_failures(mode):
    calls = []

    def handler(request):
        calls.append(request.url)
        if request.url.host == "coverartarchive.org" and mode == "matched":
            return httpx.Response(307, headers={"location": "https://archive.org/index.json"})
        if mode == "absent":
            return httpx.Response(404)
        if mode == "error":
            return httpx.Response(503)
        if mode == "malformed":
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json={
                "images": [
                    {
                        "front": mode == "matched",
                        "thumbnails": {
                            "500": f"http://coverartarchive.org/release/{RELEASE}/500.jpg",
                        },
                    }
                ]
            },
        )

    result = asyncio.run(run_with(handler, lambda c: providers.cover_art(c, RELEASE)))
    if mode == "matched":
        assert result["status"] == "matched" and result["cover_url"].startswith("https://")
        assert len(calls) == 2 and calls[-1].host == "archive.org"
    else:
        assert result["cover_url"] is None
        assert result["status"] == ("error" if mode in {"error", "malformed"} else "not_found")


@pytest.mark.parametrize(
    "artist,title", [("", "Believe"), ("GRADUR", "Comme un sheguey (feat. L2b)")]
)
def test_lastfm_key_query_pagination_and_stable_results(monkeypatch, artist, title):
    from pydantic import SecretStr

    monkeypatch.setattr(providers.settings, "lastfm_api_key", SecretStr("test-private-key"))

    def handler(request):
        assert request.url.params["api_key"] == "test-private-key"
        assert request.url.params["method"] == "track.search"
        assert request.url.params["track"] == catalog.without_feature(title)
        assert request.url.params.get("artist", "") == artist
        assert request.url.params["page"] == "2" and request.url.params["limit"] == "10"
        return httpx.Response(
            200,
            json={
                "results": {
                    "opensearch:totalResults": "21",
                    "trackmatches": {
                        "track": [
                            {
                                "name": catalog.without_feature(title),
                                "artist": artist,
                                "mbid": "",
                                "url": "http://www.last.fm/music/Artist/_/Song",
                                "image": [{"#text": ""}],
                            },
                        ]
                    },
                }
            },
        )

    result = asyncio.run(
        run_with(handler, lambda c: providers.search_recordings(c, "lastfm", artist, title, 2, 10))
    )
    assert result["has_more"]
    track = result["candidates"][0]
    assert track["url"].startswith("https://www.last.fm/") and track["cover_url"] is None
    assert len(track["provider_id"]) == 64
    assert "duration" not in track and "album" not in track and not track["genres"]
    assert "test-private-key" not in str(result)


def test_lastfm_missing_key_and_provider_errors_do_not_expose_secrets(db, client, monkeypatch):
    from pydantic import SecretStr

    entry = ensure_entry(db, "Artist", "Song")
    db.commit()
    monkeypatch.setattr(providers.settings, "lastfm_api_key", SecretStr(""))
    path = f"/api/catalog/{entry.id}/search"
    response = client.post(path, json={"provider": "lastfm", "title": "Song"}, headers=AUTH)
    assert response.status_code == 503 and "LASTFM_API_KEY" in response.json()["detail"]
    monkeypatch.setattr(providers.settings, "lastfm_api_key", SecretStr("test-private-key"))

    async def fail(*args, **kwargs):
        raise ValueError("request contained test-private-key")

    monkeypatch.setattr(providers, "fetch_json", fail)
    response = client.post(path, json={"provider": "lastfm", "title": "Song"}, headers=AUTH)
    assert response.status_code == 502 and "test-private-key" not in response.text

    # The existing providers are still independently usable.
    async def other(*args):
        return []

    monkeypatch.setattr("app.catalog_api.search_provider", other)
    assert (
        client.post(path, json={"provider": "itunes", "title": "Song"}, headers=AUTH).status_code
        == 200
    )


def test_lyrics_suggestions_do_not_claim_to_contain_lyrics_and_encode_lookup():
    calls = []

    def handler(request):
        calls.append(request.url)
        if request.url.path.startswith("/suggest/"):
            assert "GRADUR" in request.url.path and "Comme un sheguey" in request.url.path
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 42,
                            "artist": {"name": "GRADUR / L2B"},
                            "title": "Comme un sheguey?",
                            "duration": 180,
                        },
                        {"id": 43, "artist": None, "title": "Invalid"},
                    ],
                    "next": "http://127.0.0.1/private",
                },
            )
        assert request.url.raw_path == b"/v1/GRADUR%20%2F%20L2B/Comme%20un%20sheguey%3F"
        return httpx.Response(200, json={"lyrics": "Real lyrics\nSecond line"})

    async def run(c):
        suggestions = await providers.suggest_lyrics(c, "GRADUR", "Comme un sheguey (feat. L2b)")
        assert len(suggestions) == 1 and not suggestions[0]["plain"]
        assert suggestions[0]["lyrics_status"] == "pending" and not suggestions[0]["instrumental"]
        return await providers.load_lyrics(c, suggestions[0])

    result = asyncio.run(run_with(handler, run))
    assert result["plain"] == "Real lyrics\nSecond line" and result["lyrics_status"] == "matched"
    assert len(calls) == 2  # Never follow an untrusted 'next' URL.


@pytest.mark.parametrize(
    "code,body,status", [(404, {}, "not_found"), (200, {}, "not_found"), (503, {}, "error")]
)
def test_unavailable_lyrics_are_explicit(code, body, status):
    candidate = {"artist": "Artist", "title": "Song", "plain": "", "provider": "lyricsovh"}
    result = asyncio.run(
        run_with(
            lambda r: httpx.Response(code, json=body), lambda c: providers.load_lyrics(c, candidate)
        )
    )
    assert result["lyrics_status"] == status and not result["plain"]


def test_new_search_api_paging_and_artwork_preserve_primary_selection(db, client, monkeypatch):
    entry = ensure_entry(db, "Artist", "Song")
    entry.selected = {"provider": "itunes", "provider_id": "1", "title": "Song", "artist": "Artist"}
    db.commit()

    async def search(c, provider, artist, title, page, limit):
        assert provider == "musicbrainz" and page == 2 and limit == 10
        return {
            "candidates": [
                {
                    "provider": "musicbrainz",
                    "provider_id": RECORDING,
                    "artist": "Artist",
                    "title": "Song",
                    "release_id": RELEASE,
                }
            ],
            "page": page,
            "has_more": True,
        }

    monkeypatch.setattr(providers, "search_recordings", search)
    response = client.post(
        f"/api/catalog/{entry.id}/search",
        json={
            "provider": "musicbrainz",
            "artist": "Artist",
            "title": "Song",
            "page": 2,
            "limit": 10,
        },
        headers=AUTH,
    )
    assert response.status_code == 200 and response.json()["has_more"]

    async def art(*args):
        return {"status": "matched", "cover_url": "https://archive.org/cover.jpg"}

    monkeypatch.setattr(providers, "cover_art", art)
    path = f"/api/catalog/{entry.id}/candidate-artwork"
    payload = {"candidate_id": f"musicbrainz:{RECORDING}", "revision": response.json()["revision"]}
    assert client.post(path, json=payload).status_code == 401
    result = client.post(path, json=payload, headers=AUTH)
    assert result.status_code == 200, result.text
    assert result.json()["candidates"][0]["cover_url"] == "https://archive.org/cover.jpg"
    assert "cover_url" in result.json()["candidate_completions"][f"musicbrainz:{RECORDING}"]
    assert entry.selected["provider"] == "itunes"
    assert client.post(path, json=payload, headers=AUTH).status_code == 409


def test_lyrics_lookup_requires_text_before_selection_and_preserves_other_proposals(
    db, client, monkeypatch
):
    entry = ensure_entry(db, "GRADUR", "Comme un sheguey")
    entry.candidates = [
        {"provider": "itunes", "provider_id": "1", "artist": "GRADUR", "title": entry.title}
    ]
    entry.lyrics_candidates = [
        {
            "id": "lyricsovh:42",
            "provider": "lyricsovh",
            "title": entry.title,
            "artist": "GRADUR",
            "plain": "",
            "lyrics_status": "pending",
        }
    ]
    db.commit()
    correction = f"/api/catalog/{entry.id}/correction"
    assert (
        client.post(
            correction, json={"target": "lyrics", "candidate_id": "lyricsovh:42"}, headers=AUTH
        ).status_code
        == 422
    )

    async def load(c, candidate):
        return {**candidate, "plain": "Actual words", "lyrics_status": "matched"}

    monkeypatch.setattr(providers, "load_lyrics", load)
    response = client.post(
        f"/api/catalog/{entry.id}/candidate-lyrics",
        json={
            "candidate_id": "lyricsovh:42",
            "revision": entry.revision,
        },
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    assert entry.candidates[0]["provider"] == "itunes" and entry.lyrics is None
    result = client.post(
        correction, json={"target": "lyrics", "candidate_id": "lyricsovh:42"}, headers=AUTH
    )
    assert result.status_code == 200
    assert (
        result.json()["lyrics"]["provider"] == "lyricsovh"
        and entry.lyrics["plain"] == "Actual words"
    )


def test_artwork_can_use_another_known_edition_without_mislabeling_album(db, client, monkeypatch):
    entry = ensure_entry(db, "Artist", "Song")
    entry.candidates = [
        {
            "provider": "musicbrainz",
            "provider_id": RECORDING,
            "artist": "Artist",
            "title": "Song",
            "release_id": RELEASE,
            "album": "First edition",
            "releases": [
                {"id": RELEASE, "title": "First edition", "date": "2020"},
                {"id": RECORDING, "title": "Second edition", "date": "2021"},
            ],
        }
    ]
    db.commit()
    requested = []

    async def artwork(c, key):
        requested.append(key)
        return {
            "status": "matched" if key == RECORDING else "not_found",
            "cover_url": "https://archive.org/second.jpg" if key == RECORDING else None,
        }

    monkeypatch.setattr(providers, "cover_art", artwork)
    response = client.post(
        f"/api/catalog/{entry.id}/candidate-artwork",
        json={
            "candidate_id": f"musicbrainz:{RECORDING}",
            "revision": entry.revision,
        },
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    candidate = response.json()["candidates"][0]
    assert requested == [RELEASE, RECORDING]
    assert candidate["album"] == "Second edition" and candidate["release_date"] == "2021"
    assert candidate["release_id"] == RECORDING and candidate["cover_status"] == "matched"
