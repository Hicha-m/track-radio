import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.models import Station
from app.services.html_sources import HTML_ADAPTERS, HtmlConfig
from app.services.metadata import fetch_metadata
from app.services.station_config import StationConfig, apply_stations


def station(**changes):
    return Station(
        name="HTML radio",
        slug="html-radio",
        stream_url="https://example.com/live",
        source_type="html",
        metadata_url="https://example.com/current",
        **changes,
    )


def collect(value, html):
    requests = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield html

    async def run():
        def response(request):
            requests.append(request)
            return httpx.Response(200, stream=Body())

        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            return await fetch_metadata(value, 5, client)

    return asyncio.run(run()), requests


def test_html_selectors_extract_nested_text_and_entities_without_scripts():
    value = station(html={"title_selector": "#playing .title", "artist_selector": ".artist"})
    result, requests = collect(
        value,
        b"""<div id="playing">
        <b class="artist">Artist &amp; Friend</b><p class="title">A <i>song</i></p></div>
        <script>throw new Error('must never execute')</script>""",
    )
    assert (result.artist, result.title, result.source) == ("Artist & Friend", "A song", "html")
    assert str(requests[0].url) == "https://example.com/current"


def test_html_attributes_and_declared_encoding():
    value = station(html={"title_selector": 'meta[name="song"]', "title_attribute": "content"})
    result, _ = collect(
        value, '<meta charset="windows-1252"><meta name="song" content="Été">'.encode("cp1252")
    )
    assert result.title == "Été"
    assert result.artist is None


@pytest.mark.parametrize("html", [b"<p>unavailable</p>", b'<h1 class="title">Song</h1>'])
def test_html_missing_configured_element_does_not_record_partial_song(html):
    result, _ = collect(
        station(html={"title_selector": ".title", "artist_selector": ".artist"}), html
    )
    assert result is None


def test_html_metadata_limits_still_apply():
    with pytest.raises(ValueError, match="longues"):
        collect(station(html={"title_selector": "h1"}), b"<h1>" + b"x" * 501 + b"</h1>")


def test_html_structure_bounded_before_dom_allocation():
    with pytest.raises(ValueError, match="complexe"):
        collect(station(html={"title_selector": "b"}), b"<b>x</b>" * 10_001)


def test_funradio_adapter_preserves_existing_extraction():
    adapter = HTML_ADAPTERS["funradio"]
    now = datetime(2026, 10, 18, 21, 15, tzinfo=ZoneInfo("Europe/Paris"))
    assert adapter.url("https://example.com/history/", now).endswith(
        "/18-10-2026/songs?hour=21&minute=15"
    )
    result, _ = collect(
        station(html={"adapter": "funradio"}),
        b'<div data-qect-info="[]"></div><div data-qect-info="'
        b'{&quot;singer&quot;:&quot;Artist&quot;,&quot;title&quot;:&quot;Song&quot;}"></div>',
    )
    assert (result.artist, result.title) == ("Artist", "Song")


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"title_selector": "[invalid"},
        {"adapter": "arbitrary.module"},
        {"title_selector": "h1", "artist_attribute": "content"},
        {"adapter": "funradio", "title_selector": "h1"},
    ],
)
def test_invalid_html_config_rejected_before_sync(options):
    with pytest.raises(ValueError):
        HtmlConfig.model_validate(options)


def test_html_configuration_persists_and_updates(db):
    data = dict(
        slug="test",
        name="Test",
        stream_url="https://example.com/live",
        source_type="html",
        metadata_url="https://example.com/current",
    )
    with pytest.raises(ValueError, match="obligatoire"):
        StationConfig(**data)
    value = StationConfig(**data, html={"title_selector": ".title"})
    apply_stations(db, [value])
    db.expire_all()
    saved = db.query(Station).one()
    assert saved.html["title_selector"] == ".title"
    value.html.title_selector = "h1"
    apply_stations(db, [value])
    db.expire_all()
    assert saved.html["title_selector"] == "h1"
