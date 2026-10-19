import io
import os
import socket
import subprocess
import sys
import tempfile
import time
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="radio-browser-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/browser.db"
    os.environ["ADMIN_TOKEN"] = "browser-test-only"
    from app.database import SessionLocal, engine
    from app.models import Base, Play, Station
    from app.services.catalog import ensure_entry

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        station = Station(name="Radio Test", slug="test", stream_url="https://example.com/live")
        db.add(station)
        db.flush()
        for i, title in enumerate(
            ["Soon (ft Gaetan Roussel)", "Track Two", "Track Three", "Track Four", "Track Five"]
        ):
            now = datetime.now(UTC) - timedelta(minutes=i)
            db.add(
                Play(
                    station_id=station.id,
                    artist="Mosimann",
                    title=title,
                    raw_metadata=title,
                    source="icy",
                    detected_at=now,
                    last_seen_at=now,
                )
            )
            entry = ensure_entry(db, "Mosimann", title)
            entry.status = "matched"
            entry.selected = {
                "provider": "deezer",
                "provider_id": str(i + 1),
                "title": title,
                "artist": "Mosimann",
                "duration": 151,
                "isrc": f"FRTEST{i}",
                "cover_url": "https://cdn-images.dzcdn.net/test-cover.svg"
                if i != 3
                else "https://cdn-images.dzcdn.net/broken.svg",
                "preview_url": "https://cdnt-preview.dzcdn.net/test.mp3",
                "url": "https://www.deezer.com/track/1",
            }
        db.commit()
    log = open(f"{directory}/server.log", "w")
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=root,
        env=os.environ,
        stdout=log,
        stderr=log,
    )
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.1)
        audio_buffer = io.BytesIO()
        with wave.open(audio_buffer, "wb") as sound:
            sound.setnchannels(1)
            sound.setsampwidth(2)
            sound.setframerate(8000)
            sound.writeframes(b"\x00\x00" * 8000 * 120)
        with sync_playwright() as p:
            engines = os.getenv("RADIO_BROWSER_ENGINES", "chromium").split(",")
            if not set(engines) <= {"chromium", "webkit", "firefox"}:
                raise ValueError("RADIO_BROWSER_ENGINES must name Playwright browser engines")
            browsers = [getattr(p, name).launch() for name in engines]
            try:
                for browser in browsers:
                    for width, height in [(1280, 900), (402, 874), (412, 915)]:
                        page = browser.new_page(viewport={"width": width, "height": height})
                        errors = []
                        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                        page.route(
                            "https://example.com/live",
                            lambda route: route.fulfill(
                                content_type="audio/wav", body=audio_buffer.getvalue()
                            ),
                        )

                        def cover(route):
                            if "broken.svg" in route.request.url:
                                route.fulfill(status=404, body="Missing artwork")
                            else:
                                route.fulfill(
                                    content_type="image/svg+xml",
                                    body=(
                                        '<svg xmlns="http://www.w3.org/2000/svg" '
                                        'width="500" height="500"><rect width="500" '
                                        'height="500" fill="#e82d49"/></svg>'
                                    ),
                                )

                        page.route("https://cdn-images.dzcdn.net/**", cover)
                        page.goto(base_url + "/")
                        expect(page.locator(".podium-card")).to_have_count(3)
                        expect(page.locator(".cover-rank")).to_have_text(["01", "02", "03"])
                        # Include loaded artwork and a failed image with its fallback.
                        page.locator(".podium").scroll_into_view_if_needed()
                        expect(page.locator(".cover-2 .cover-image")).to_have_count(0)
                        geometry = page.locator(".podium-card .cover").evaluate_all(
                            """nodes => nodes.every(e => {
                            const rect = e.getBoundingClientRect();
                            const rank = e.querySelector('.cover-rank').getBoundingClientRect();
                            return rect.right <= innerWidth && rect.left >= 0
                                && Math.abs(rect.width-rect.height) < 1
                                && rank.left >= rect.left && rank.right <= rect.right
                                && rank.top >= rect.top && rank.bottom <= rect.bottom;
                        })"""
                        )
                        assert geometry, (browser.browser_type.name, width, "cover/rank overflow")
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= innerWidth"
                        ), width
                        page.locator(".station-play").click()
                        page.wait_for_function(
                            """() => {
                                const player = document.querySelector('#radio-player');
                                return !player.paused && player.currentTime > 0.1;
                            }"""
                        )
                        page.evaluate("""() => {
                            window.retainedPlayer = document.querySelector('#radio-player');
                            window.pauseEvents = 0;
                            window.loadEvents = 0;
                            retainedPlayer.addEventListener('pause', () => pauseEvents++);
                            retainedPlayer.addEventListener('loadstart', () => loadEvents++);
                            window.initialTime = retainedPlayer.currentTime;
                        }""")

                        def assert_uninterrupted(page=page, width=width):
                            assert page.evaluate("""() =>
                                document.querySelector('#radio-player') === window.retainedPlayer
                                && !retainedPlayer.paused
                                && retainedPlayer.currentTime >= initialTime
                                && pauseEvents === 0 && loadEvents === 0"""), (
                                width,
                                "audio interrupted",
                            )
                            assert page.evaluate(
                                "document.documentElement.scrollWidth <= innerWidth"
                            ), width

                        navigation = page.get_by_role("navigation", name="Navigation principale")
                        navigation.get_by_role("link", name="Recherche avancée").click()
                        expect(page.locator("h1")).to_have_text("Recherche avancée")
                        expect(page.locator("#results tbody tr")).to_have_count(5)
                        assert_uninterrupted()
                        navigation.get_by_role("link", name="Classements").click()
                        expect(page.locator("h1")).to_have_text("Classements")
                        assert_uninterrupted()
                        page.locator("#chart-list .chart-row").first.click()
                        expect(page.locator(".catalog-dialog")).to_be_visible()
                        assert_uninterrupted()
                        page.keyboard.press("Escape")
                        expect(page.locator(".catalog-dialog")).not_to_be_visible()
                        page.keyboard.press("Control+k")
                        expect(page.locator(".music-search-dialog")).to_be_visible()
                        page.keyboard.press("Escape")
                        page.locator(".quick-search-toggle").click()
                        expect(page.locator(".music-search-dialog")).to_be_visible()
                        page.locator(".music-search-dialog input").fill("Mosimann Soon")
                        expect(page.locator(".music-search-result")).to_have_count(1)
                        page.screenshot(
                            path=f"/tmp/radio-search-{browser.browser_type.name}-{width}.png"
                        )
                        assert_uninterrupted()
                        page.locator(".music-search-result").click()
                        expect(page.locator(".catalog-dialog")).to_be_visible()
                        expect(page.locator(".music-search-dialog")).not_to_be_visible()
                        assert_uninterrupted()
                        page.keyboard.press("Escape")
                        # Click through a track and follow browser history in both directions.
                        page.locator(".podium-detail").first.click()
                        expect(page.locator(".catalog-dialog")).to_be_visible()
                        page.locator(".catalog-search-link").click()
                        expect(page.locator("h1")).to_have_text("Recherche avancée")
                        expect(page.locator("#results tbody tr")).to_have_count(1)
                        assert_uninterrupted()
                        page.go_back()
                        expect(page.locator("h1")).to_have_text("Classements")
                        assert_uninterrupted()
                        page.go_forward()
                        expect(page.locator("h1")).to_have_text("Recherche avancée")
                        assert_uninterrupted()
                        page.locator("#player-mute").click()
                        assert page.locator("#radio-player").evaluate("(e) => e.muted")
                        page.locator("#player-mute").click()
                        assert not page.locator("#radio-player").evaluate("(e) => e.muted")
                        page.locator("#player-toggle").click()
                        assert page.locator("#radio-player").evaluate("(e) => e.paused")
                        page.locator("#player-toggle").click()
                        page.wait_for_function('!document.querySelector("#radio-player").paused')
                        navigation.get_by_role("link", name="Classements").click()
                        expect(page.locator("h1")).to_have_text("Classements")
                        page.route(
                            "**/api/catalog/*/preview",
                            lambda route: route.fulfill(
                                content_type="audio/wav", body=audio_buffer.getvalue()
                            ),
                        )
                        page.locator("#chart-list .preview-button").first.click()
                        expect(page.locator("#chart-list .preview-button").first).to_have_attribute(
                            "aria-pressed", "true"
                        )
                        assert page.locator("#radio-player").evaluate("e => e.paused")
                        expect(page.locator(".catalog-dialog")).not_to_be_visible()
                        page.locator("#chart-list .preview-button").first.click()
                        expect(page.locator(".row-preview-audio")).to_have_count(0)
                        page.locator("#player-toggle").click()
                        page.wait_for_function('!document.querySelector("#radio-player").paused')
                        # A failed page request must leave the live player running.
                        page.route(
                            "**/recherche",
                            lambda route: route.fulfill(status=503, body="Unavailable"),
                        )
                        page.locator(".topbar-actions > a").click()
                        expect(page.locator("#navigation-status")).to_contain_text("indisponible")
                        assert page.evaluate(
                            'document.querySelector("#radio-player") === window.retainedPlayer '
                            "&& !retainedPlayer.paused"
                        )
                        page.unroute("**/recherche")
                        navigation.get_by_role("link", name="Classements").click()
                        expect(page.locator("h1")).to_have_text("Classements")
                        expect(page.locator("#page-content")).not_to_have_attribute(
                            "aria-busy", "true"
                        )
                        expect(page.locator(".podium-card")).to_have_count(3)
                        page.locator(".podium").scroll_into_view_if_needed()
                        page.screenshot(path=f"/tmp/radio-{browser.browser_type.name}-{width}.png")
                        assert not errors, errors
                        print(
                            {
                                "browser": browser.browser_type.name,
                                "viewport": f"{width}x{height}",
                                "ranks_and_square_covers": "passed",
                                "uninterrupted_navigation": "passed",
                                "controls_and_failure": "passed",
                            },
                            flush=True,
                        )
                        page.close()
            finally:
                for browser in browsers:
                    browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
        log.close()
