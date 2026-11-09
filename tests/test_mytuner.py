"""Signed artwork, matching, bounded execution and optional-provider failures."""

import asyncio
import json

import httpx
import pytest
import quickjs

from app.models import Station
from app.services import mytuner
from app.services.metadata import TrackMetadata

COVER = "https://example.test/temporary.jpg"
AUTH = "HMAC radiofrance_web:" + "A" * 43 + "="  # Synthetic, never a provider credential.
SIGNER = """
function DG(id) { this.execute = function(time, kind) {
 if (typeof fetch !== 'undefined' || typeof process !== 'undefined'
     || typeof require !== 'undefined') throw new Error('Host access');
 if (id !== 408813 || time < 1 || kind !== 'metadata') throw new Error('Wrong request');
 return 'HMAC radiofrance_web:' + 'A'.repeat(43) + '=';
}; }
"""


def response(label="Artist - Song", **overrides):
    return {
        "error_code": 0,
        "no_metadata_available": False,
        "radio_metadata": {"metadata": label, "artwork_url_large": COVER},
        **overrides,
    }


def test_only_matching_track_can_supply_temporary_cover():
    song = TrackMetadata("Artist", "Song", "fixture", "icy")
    assert mytuner.matching_cover(response(), song) == COVER
    assert mytuner.matching_cover(response("Another - Song"), song) is None
    assert mytuner.matching_cover(response("Artist - Another song"), song) is None
    assert mytuner.matching_cover(response(error_code=14), song) is None
    assert mytuner.matching_cover(response(no_metadata_available=True), song) is None
    assert mytuner.matching_cover(response(radio_metadata=None), song) is None
    assert (
        mytuner.matching_cover(
            response(
                radio_metadata={"metadata": "Artist - Song", "artwork_url_large": "javascript:x"}
            ),
            song,
        )
        is None
    )


def test_public_signer_has_no_host_io_and_checks_output_shape():
    assert mytuner.authorization("", SIGNER, 408813, 123) == AUTH
    large_clock = SIGNER.replace("time < 1", "time !== 1800000000123")
    assert mytuner.authorization("", large_clock, 408813, 1800000000123) == AUTH
    unpadded = SIGNER.replace("+ '='", "")
    assert mytuner.authorization("", unpadded, 408813, 123) == AUTH[:-1]
    with pytest.raises(ValueError, match="Invalid provider authorization"):
        mytuner.authorization("", 'function DG(){this.execute=()=>"bad\\r\\nHeader: x"}', 1, 123)
    with pytest.raises(quickjs.JSException):
        mytuner.authorization("while(true){}", SIGNER, 408813, 123)


def test_signed_request_caches_only_matching_artwork_and_renews_time(monkeypatch):
    monkeypatch.setattr(mytuner, "_assets", None)
    monkeypatch.setattr(mytuner, "_assets_until", 0)
    monkeypatch.setattr(mytuner, "_observations", {})
    calls = []
    ticks = iter([123.0, 124.0])
    monkeypatch.setattr(mytuner, "time", lambda: next(ticks))

    def respond(request):
        calls.append(request)
        if str(request.url) == mytuner.CRYPTO_URL:
            content = b""
        elif str(request.url) == mytuner.SIGNER_URL:
            content = SIGNER.encode()
        else:
            assert request.headers["Authorization"] == AUTH
            assert request.url.params["radio_id"] == "408813"
            content = json.dumps(response()).encode()
        return httpx.Response(200, stream=httpx.ByteStream(content))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            radio = Station(slug="europe2")
            song = TrackMetadata("Artist", "Song", "fixture", "icy")
            assert await mytuner.cover_for(radio, song, client) == COVER
            assert await mytuner.cover_for(radio, song, client) == COVER
            wrong = TrackMetadata("Artist", "New song", "fixture", "icy")
            assert await mytuner.cover_for(radio, wrong, client) is None
            assert len(calls) == 3  # Two public assets and one signed observation.
            mytuner._observations.clear()
            assert await mytuner.cover_for(radio, song, client) == COVER
            assert len(calls) == 4
            times = [
                r.url.params["time"] for r in calls if r.url.host == "metadata-api.mytuner.mobi"
            ]
            assert times == ["123000", "124000"]
            assert set(mytuner._observations[408813]) == {"until", "cover", "identity"}

    asyncio.run(run())


@pytest.mark.parametrize("status", [403, 429, 503])
def test_unavailable_provider_does_not_break_collection(monkeypatch, status):
    monkeypatch.setattr(mytuner, "_assets", None)
    monkeypatch.setattr(mytuner, "_assets_until", 0)
    monkeypatch.setattr(mytuner, "_observations", {})

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(status))
        ) as client:
            assert (
                await mytuner.cover_for(
                    Station(slug="europe2"),
                    TrackMetadata("Artist", "Song", "fixture", "icy"),
                    client,
                )
                is None
            )
            assert mytuner._observations[408813]["cover"] is None

    asyncio.run(run())
