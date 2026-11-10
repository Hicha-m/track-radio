import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import free_media as media

TRACK = "https://open.spotify.com/track/4cSHMHSTRhetAtT1iDP2zO"
IMAGE = "https://i.scdn.co/image/ab67616d0000b273" + "a" * 32
THUMB = "https://image-cdn-fa.spotifycdn.com/image/ab67616d00001e02" + "a" * 32
VIDEO = {"id": "aaaaaaaaaaa", "title": "Artist - Song (Official Video)", "channel": "Artist"}


def page(artist="Artist", title="Song", album="Album", image=IMAGE):
    return f'''<head><meta property="og:title" content="{title}">
<meta property="og:description" content="{artist} · {album} · Song · 2026">
<meta property="og:image" content="{image}"></head>'''


def test_only_canonical_spotify_tracks_and_images_are_allowed():
    assert media.spotify_track(TRACK + "?si=ignored") == TRACK
    assert media.spotify_track(TRACK.replace("/track/", "/intl-fr/track/")) == TRACK
    for url in [
        "https://example.com/track/" + "a" * 22,
        TRACK.replace("https:", "http:"),
        TRACK.replace("open.spotify.com", "open.spotify.com@127.0.0.1"),
        TRACK + "/extra",
        TRACK.replace("/track/", "/album/"),
        TRACK.replace("open.spotify.com", "open.spotify.com:444"),
    ]:
        assert media.spotify_track(url) is None
    assert media.spotify_image(IMAGE) == IMAGE
    assert media.spotify_image(THUMB) == THUMB
    for url in [
        IMAGE.replace("i.scdn.co", "127.0.0.1"),
        IMAGE.replace("https:", "http:"),
        IMAGE.replace("/image/", "/redirect/"),
        IMAGE.replace("i.scdn.co", "i.scdn.co.evil.example"),
    ]:
        assert media.spotify_image(url) is None


@pytest.mark.parametrize(
    "artist,title,album,thumbnail,accepted",
    [
        ("Artist", "Song", "Album", THUMB, True),
        ("Other artist", "Song", "Album", THUMB, False),
        ("Artist", "Song (Live)", "Album", THUMB, False),
        ("Artist", "Song", "Compilation", THUMB, False),
        ("Artist", "Song", "Album", THUMB[:-1] + "b", False),
    ],
)
def test_spotify_checks_page_identity_album_and_oembed(artist, title, album, thumbnail, accepted):
    def respond(request):
        if request.url.path == "/oembed":
            return httpx.Response(200, json={"title": "Song", "thumbnail_url": thumbnail})
        return httpx.Response(
            200, text=page(artist, title, album), headers={"content-type": "text/html"}
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await media.spotify_cover(client, "Artist", "Song", {"album": "Album"}, TRACK)

    result = asyncio.run(run())
    assert bool(result) is accepted
    if result:
        assert result == {"url": IMAGE, "source": "ddgs_spotify", "page": TRACK, "album": "Album"}


def test_search_snippet_is_not_enough_and_next_spotify_result_is_checked(monkeypatch):
    urls = [TRACK, TRACK.replace("4cSHMHSTRhetAtT1iDP2zO", "b" * 22)]

    async def tool(kind, query):
        assert kind == "web" and query.endswith("site:open.spotify.com/track")
        return [{"url": "https://127.0.0.1/secret"}, *({"url": url} for url in urls)]

    calls = []

    def respond(request):
        calls.append(str(request.url))
        if request.url.path == "/oembed":
            return httpx.Response(200, json={"title": "Song", "thumbnail_url": THUMB})
        return httpx.Response(
            200,
            text=page(artist="Wrong" if str(request.url) == TRACK else "Artist"),
            headers={"content-type": "text/html"},
        )

    monkeypatch.setattr(media, "run_tool", tool)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await media.Lookup(client, None).cover("Artist", "Song", {})

    result = asyncio.run(run())
    assert result["cover"]["page"] == urls[1]
    assert len(calls) == 3 and all("127.0.0.1" not in call for call in calls)


def test_youtube_rejects_extra_version_and_requires_verified_official_channel(monkeypatch):
    async def tool(*args):
        return [
            VIDEO,
            {"id": "bbbbbbbbbbb", "title": "Artist - Song Part Two", "channel": "Artist"},
            {"id": "ccccccccccc", "title": "Artist - Song (Official Video)", "channel": "Uploader"},
        ]

    verified = []

    async def verify(artist, title, url):
        verified.append(url)
        return VIDEO

    monkeypatch.setattr(media, "run_tool", tool)
    monkeypatch.setattr(media, "verify_link", verify)
    result = asyncio.run(media.Lookup(None, None).youtube("Artist", "Song"))
    assert result["video"] == {**VIDEO, "source": "yt_dlp_oembed"}
    assert verified == ["https://youtu.be/aaaaaaaaaaa"]


def test_tied_official_candidates_stay_ambiguous(monkeypatch):
    rows = [VIDEO, {**VIDEO, "id": "bbbbbbbbbbb"}]

    async def tool(*args):
        return rows

    async def verify(artist, title, url):
        return next(row for row in rows if url.endswith(row["id"]))

    monkeypatch.setattr(media, "run_tool", tool)
    monkeypatch.setattr(media, "verify_link", verify)
    result = asyncio.run(media.Lookup(None, None).youtube("Artist", "Song"))
    assert result["status"] == "ambiguous" and result["video"] is None


def test_oembed_cannot_change_the_verified_version(monkeypatch):
    async def tool(*args):
        return [VIDEO]

    async def verify(*args):
        return {**VIDEO, "title": "Artist - Song (Extended)"}

    monkeypatch.setattr(media, "run_tool", tool)
    monkeypatch.setattr(media, "verify_link", verify)
    result = asyncio.run(media.Lookup(None, None).youtube("Artist", "Song"))
    assert result["video"] is None


def test_free_sources_fill_media_without_musicbrainz(monkeypatch):
    async def youtube(*args):
        return {"status": "matched", "video": VIDEO}

    async def cover(*args):
        return {"status": "matched", "cover": {"url": IMAGE, "source": "ddgs_spotify"}}

    lookup = media.Lookup(None, SimpleNamespace())
    monkeypatch.setattr(lookup, "youtube", youtube)
    monkeypatch.setattr(lookup, "cover", cover)
    result = asyncio.run(lookup.find("Artist", "Song", {}, want_cover=True, want_video=True))
    assert result["video"] == VIDEO and result["cover"]["url"] == IMAGE
    assert "musicbrainz" not in result["sources"]


def test_failed_source_keeps_other_media_and_uses_fallback_with_backoff(monkeypatch):
    async def youtube(*args):
        raise media.SearchUnavailable

    async def cover(*args):
        return {"status": "matched", "cover": {"url": IMAGE}}

    fallback_calls = []

    async def fallback(*args, **kwargs):
        fallback_calls.append(kwargs)
        return {"status": "not_found"}

    lookup = media.Lookup(None, SimpleNamespace(find=fallback))
    monkeypatch.setattr(lookup, "youtube", youtube)
    monkeypatch.setattr(lookup, "cover", cover)
    result = asyncio.run(lookup.find("Artist", "Song", {}, want_cover=True, want_video=True))
    assert result["status"] == "error" and result["cover"]["url"] == IMAGE
    assert fallback_calls == [{"want_cover": False, "want_video": True}]
    assert lookup.pauses["youtube"] > media.monotonic()


def test_process_cancellation_kills_child_and_does_not_forward_secrets(monkeypatch):
    async def run():
        started = asyncio.Event()

        class Process:
            returncode = None

            async def communicate(self, body):
                assert json.loads(body)["kind"] == "youtube"
                started.set()
                await asyncio.Event().wait()

            def kill(self):
                self.returncode = -9

            async def wait(self):
                return self.returncode

        process = Process()

        async def create(*args, **kwargs):
            assert args[1:] == ("-m", "app.media_search_tools")
            assert "ADMIN_TOKEN" not in kwargs["env"] and "YOUTUBE_API_KEY" not in kwargs["env"]
            return process

        monkeypatch.setenv("ADMIN_TOKEN", "secret-test-value")
        monkeypatch.setenv("YOUTUBE_API_KEY", "secret-test-value")
        monkeypatch.setattr(media.asyncio, "create_subprocess_exec", create)
        task = asyncio.create_task(media.run_tool("youtube", "Artist Song"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.returncode == -9

    asyncio.run(run())


def test_process_timeout_reaps_the_child(monkeypatch):
    actual_timeout = asyncio.timeout
    monkeypatch.setattr(media.asyncio, "timeout", lambda duration: actual_timeout(0.01))

    class Process:
        returncode = None

        async def communicate(self, body):
            await asyncio.Event().wait()

        def kill(self):
            self.returncode = -9

        async def wait(self):
            return self.returncode

    process = Process()

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr(media.asyncio, "create_subprocess_exec", create)
    with pytest.raises(media.SearchUnavailable):
        asyncio.run(media.run_tool("web", "Artist Song"))
    assert process.returncode == -9


def test_child_adapters_never_download_media_or_use_automatic_backends(monkeypatch):
    import sys

    from app import media_search_tools as tools

    calls = []

    class Youtube:
        def __init__(self, options):
            assert options["extract_flat"] and options["skip_download"]
            assert options["cachedir"] is False and options["playlistend"] == 3

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, query, download):
            calls.append(query)
            assert download is False
            return {"entries": [{"id": "aaaaaaaaaaa", "title": "Song", "channel": "Artist"}]}

    class Search:
        def __init__(self, timeout):
            assert timeout == 8

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def text(self, query, **options):
            assert options == {"backend": "yahoo,duckduckgo", "max_results": 3}
            return [{"href": TRACK, "title": "snippet is ignored"}]

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=Youtube))
    import ddgs

    monkeypatch.setattr(ddgs, "DDGS", Search)
    assert tools.youtube_rows("Artist Song")[0]["id"] == "aaaaaaaaaaa"
    assert calls == ["ytsearch3:Artist Song"]
    assert tools.web_rows("Artist Song") == [{"url": TRACK}]


def test_spotify_accented_labels_are_decoded_as_utf8():
    def respond(request):
        if request.url.path == "/oembed":
            return httpx.Response(200, json={"title": "Été avec toi", "thumbnail_url": THUMB})
        return httpx.Response(
            200,
            content=page(artist="Adèle Castillon", title="Été avec toi").encode("utf-8"),
            headers={"content-type": "text/html; charset=utf-8"},
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await media.spotify_cover(client, "Adèle Castillon", "Été avec toi", {}, TRACK)

    assert asyncio.run(run())["url"] == IMAGE


def test_spotify_streamed_metadata_after_head_is_still_checked():
    class Streaming(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"<head><title>Spotify</title></head><body>"
            yield page().encode()

    def respond(request):
        if request.url.path == "/oembed":
            return httpx.Response(200, json={"title": "Song", "thumbnail_url": THUMB})
        return httpx.Response(200, stream=Streaming(), headers={"content-type": "text/html"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await media.spotify_cover(client, "Artist", "Song", {}, TRACK)

    assert asyncio.run(run())["url"] == IMAGE


def test_spotify_album_art_identifier_is_independent_of_resolution():
    original = "https://i.scdn.co/image/ab67616d0000b27343db3f10cf8ceb525ef193ac"
    thumbnail = "https://image-cdn-fa.spotifycdn.com/image/ab67616d00001e0243db3f10cf8ceb525ef193ac"
    assert media.artwork_id(original) == media.artwork_id(thumbnail)
    assert media.artwork_id(original) != media.artwork_id(thumbnail[:-1] + "b")


def test_disabled_ddgs_backends_do_not_trigger_an_automatic_fallback(monkeypatch):
    import ddgs.engines

    from app import media_search_tools as tools

    monkeypatch.setattr(ddgs.engines, "ENGINES", {"text": {}})
    with pytest.raises(ValueError, match="No configured"):
        tools.web_rows("Artist Song")
