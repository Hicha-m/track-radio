import asyncio

import httpx
import pytest

from app.config import settings
from app.models import Station
from app.services.metadata import fetch_metadata


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, delay=0):
        self.chunks = chunks
        self.delay = delay

    async def __aiter__(self):
        for chunk in self.chunks:
            await asyncio.sleep(self.delay)
            yield chunk


def run_fetch(response, station=None, timeout=1):
    station = station or Station(stream_url="https://radio.example/live", source_type="icy")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as c:
            return await fetch_metadata(station, timeout, c)

    return asyncio.run(run())


def test_reject_huge_icy_interval_before_reading():
    with pytest.raises(ValueError, match="ICY"):
        run_fetch(httpx.Response(200, headers={"icy-metaint": "999999999"}, stream=Chunks([])))


def test_icy_metadata_and_no_audio_saved():
    raw = b"StreamTitle='Artist - Track';"
    padded = raw.ljust(32, b"\0")
    metadata = run_fetch(
        httpx.Response(
            200, headers={"icy-metaint": "4"}, stream=Chunks([b"aaaa" + b"\x02" + padded])
        )
    )
    assert (metadata.artist, metadata.title) == ("Artist", "Track")


@pytest.mark.parametrize("source_type", ["json", "html"])
def test_web_body_without_content_length_is_bounded(monkeypatch, source_type):
    monkeypatch.setattr(settings, "max_response_bytes", 4096)
    station = Station(
        source_type=source_type,
        metadata_url="https://radio.example/current",
        json_title_path="title",
        html={"title_selector": ".title"},
    )
    with pytest.raises(ValueError, match="volumineuse"):
        run_fetch(httpx.Response(200, stream=Chunks([b"x" * 8192])), station)


def test_reject_compressed_response():
    with pytest.raises(ValueError, match="Compression"):
        run_fetch(httpx.Response(200, headers={"content-encoding": "gzip"}, stream=Chunks([])))


def test_total_deadline_even_when_data_keeps_arriving():
    with pytest.raises(TimeoutError):
        run_fetch(
            httpx.Response(
                200, headers={"icy-metaint": "65536"}, stream=Chunks([b"x"] * 100, delay=0.01)
            ),
            timeout=0.03,
        )
