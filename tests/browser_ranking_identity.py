"""Isolated desktop/mobile regression for ranking identity and detail scope."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

with tempfile.TemporaryDirectory(prefix="radio-ranking-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/test.db"
    os.environ["ADMIN_TOKEN"] = "browser-test-only"
    from app.database import SessionLocal, engine
    from app.models import Base, GlobalExclusion, Play, Station
    from app.services.catalog import ensure_entry
    from app.services.exclusions import exclusion_id, normalized_pair

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        stations = [
            Station(name=f"Radio {i}", slug=f"radio-{i}", stream_url="https://example.test/live")
            for i in range(3)
        ]
        db.add_all(stations)
        db.flush()
        now = datetime.now(UTC) - timedelta(minutes=5)
        for index, (title, count, station, at) in enumerate(
            [
                ("Te rencontrer encore", 3, 0, now),
                ("TE RENCONTRER ENCORE", 2, 2, now),
                ("Te Rencontrer Encore", 4, 0, now - timedelta(days=30)),
                ("Te rencontrer encore (Live)", 1, 0, now),
                ("Hidden", 10, 0, now),
            ]
        ):
            entry = ensure_entry(db, "ESTL", title)
            entry.selected = {
                "provider": "deezer",
                "provider_id": str(index),
                "artist": "ESTL",
                "title": title,
                "isrc": "FRPZ92620190",
            }
            for i in range(count):
                db.add(
                    Play(
                        station_id=stations[station].id,
                        artist="ESTL",
                        title=title,
                        detected_at=at + timedelta(seconds=i),
                        last_seen_at=at + timedelta(seconds=i),
                        source="json",
                        raw_metadata=f"original {index}/{i}",
                    )
                )
        artist, title = normalized_pair("ESTL", "Hidden")
        db.add(
            GlobalExclusion(
                id=exclusion_id(artist, title),
                artist=artist,
                title=title,
                artist_normalized=artist,
                title_normalized=title,
            )
        )
        db.commit()
    from tests.browser_prepared import start as start_prepared_rankings

    stop_prepared_rankings = start_prepared_rankings(SessionLocal)
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.1)
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=os.environ.get("CHROME_EXECUTABLE", "/usr/bin/google-chrome")
            )
            for width in (1280, 414, 320):
                context = browser.new_context(viewport={"width": width, "height": 900})
                context.route("**/api/stations/*/artwork", lambda route: route.fulfill(status=404))
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                page.goto(base + "/?station_ids=1,3")
                expect(page.locator("#chart-podium article")).to_have_count(2)
                expect(page.locator(".play-count").first).to_have_text("5 diffusions")
                page.locator(".podium-detail").first.click()
                expect(page.locator(".catalog-counts strong").first).to_have_text("5")
                expect(page.locator(".catalog-counts strong").nth(1)).to_have_text("2")
                expect(page.locator(".catalog-radio .hint")).to_contain_text("radios sélectionnées")
                expect(page.locator(".catalog-radio .hint")).to_contain_text("Du ")
                Path("/tmp/radio-ranking-evidence").mkdir(exist_ok=True)
                page.locator(".catalog-radio").scroll_into_view_if_needed()
                page.screenshot(path=f"/tmp/radio-ranking-evidence/detail-{width}.png")
                assert page.locator(".catalog-dialog").evaluate("e=>e.scrollWidth<=e.clientWidth")
                page.locator(".catalog-search-link").click()
                expect(page.locator("#result-view")).to_have_value("plays")
                expect(page.locator("#results tbody tr")).to_have_count(5)
                assert "station_ids=1%2C3" in page.url and "track_id=" in page.url
                page.goto(base + "/?days=all&station_ids=1,3")
                expect(page.locator(".play-count").first).to_have_text("9 diffusions")
                page.locator(".podium-detail").first.click()
                expect(page.locator(".catalog-counts strong").first).to_have_text("9")
                expect(page.locator(".catalog-radio .hint")).to_contain_text("Tout l’historique")
                page.locator(".catalog-close").click()
                if width <= 760:
                    page.locator("#chart-controls > summary").click()
                page.locator("[data-open-export]").click()
                page.locator('dialog [name="format"]').select_option("json")
                page.locator('dialog [name="field"][value="plays"]').check()
                with page.expect_download() as downloaded:
                    page.locator('dialog [type="submit"]').click()
                data = json.loads(Path(downloaded.value.path()).read_text())
                assert [row["plays"] for row in data["items"]] == [9, 1]
                page.locator("[data-export-close]").click()
                page.goto(base + "/?station_id=1")
                expect(page.locator(".play-count").first).to_have_text("3 diffusions")
                page.locator(".podium-detail").first.click()
                expect(page.locator(".catalog-counts strong").first).to_have_text("3")
                expect(page.locator(".catalog-radio .hint")).to_contain_text("radio sélectionnée")
                page.locator(".catalog-footer button").click()
                expect(page.locator("#chart-podium article")).to_have_count(1)
                expect(page.locator("#chart-podium")).to_contain_text("(Live)")
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                assert not errors, errors
                context.close()
            browser.close()
        print(
            "Ranking identity: desktop/mobile, periods, radio sets, history, exports and hiding OK."
        )
    finally:
        stop_prepared_rankings()
        server.terminate()
        server.wait(timeout=10)
