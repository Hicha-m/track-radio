import asyncio

import httpx

from app.artwork_api import discover_image, image_from_html
from app.models import Station


def test_official_artwork_resolves_relative_url_and_rejects_private_destination():
    assert (
        image_from_html(
            '<meta property="og:image" content="/logo.jpg">', "https://radio.example/live"
        )
        == "https://radio.example/logo.jpg"
    )
    assert (
        image_from_html(
            '<meta property="og:image" content="https://127.0.0.1/private">'
            '<link rel="icon" href="/icon.png">',
            "https://radio.example/",
        )
        == "https://radio.example/icon.png"
    )
    assert (
        image_from_html(
            '<meta property="og:image" content="javascript:alert(1)">', "https://radio.example/"
        )
        is None
    )


def test_artwork_ignores_json_and_limits_page_download():
    async def run():
        for content_type, body, expected in [
            ("application/json", '{"image": "https://radio.example/wrong.jpg"}', None),
            (
                "text/html",
                " " * (256 * 1024) + '<meta property="og:image" content="/late.jpg">',
                None,
            ),
            (
                "text/html",
                '<meta property="og:image" content="/logo.jpg">',
                "https://radio.example/logo.jpg",
            ),
        ]:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request, body=body, content_type=content_type: httpx.Response(
                        200, text=body, headers={"content-type": content_type}
                    )
                )
            ) as client:
                assert await discover_image(client, "https://radio.example/") == expected

    asyncio.run(run())


def test_station_artwork_is_cached_and_missing_site_is_optional(client, db, monkeypatch):
    from app import artwork_api

    artwork_api._cache.clear()
    calls = []

    async def discover(client, site):
        calls.append(site)
        return "https://radio.example/logo.jpg"

    monkeypatch.setattr(artwork_api, "discover_image", discover)
    station = Station(
        name="Test",
        slug="test",
        stream_url="https://radio.example/live",
        website_url="https://radio.example/",
    )
    db.add(station)
    db.commit()
    for _ in range(2):
        response = client.get(f"/api/stations/{station.id}/artwork", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "https://radio.example/logo.jpg"
    assert calls == ["https://radio.example/"]
    station.website_url = None
    db.commit()
    assert client.get(f"/api/stations/{station.id}/artwork").status_code == 404
    assert client.get("/api/stations/999/artwork").status_code == 404
