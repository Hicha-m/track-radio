"""Manual ranking/export station-set check with an isolated database."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

with tempfile.TemporaryDirectory(prefix="radio-station-set-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/test.db"
    os.environ["ADMIN_TOKEN"] = "browser-test-only"
    from app.database import SessionLocal, engine
    from app.models import Base, Play, Station
    from app.services.catalog import ensure_entry

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        for i in range(3):
            station = Station(
                name=f"Radio {i}", slug=f"radio-{i}", stream_url="https://example.test"
            )
            db.add(station)
            db.flush()
            entry = ensure_entry(db, "Artist", f"Song {i}")
            entry.selected = {
                "provider": "itunes",
                "provider_id": str(i),
                "title": f"Song {i}",
                "artist": "Artist",
                "cover_url": None
                if i == 0
                else "https://example.test/missing.svg"
                if i == 1
                else "https://example.test/cover.svg",
            }
            for _ in range(i + 1):
                db.add(
                    Play(
                        station_id=station.id,
                        artist="Artist",
                        title=f"Song {i}",
                        source="icy",
                        raw_metadata="original",
                        detected_at=datetime.now(UTC),
                        last_seen_at=datetime.now(UTC),
                    )
                )
        db.commit()
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        stdout=open("/tmp/radio-station-selection-server.log", "w"),
        stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.1)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get("CHROME_EXECUTABLE"))
            for width in (1280, 414, 412, 320):
                context = browser.new_context(
                    viewport={"width": width, "height": 883 if width == 412 else 846}
                )
                context.route("**/api/stations/*/artwork", lambda route: route.fulfill(status=404))
                context.route(
                    "https://example.test/cover.svg",
                    lambda route: route.fulfill(
                        content_type="image/svg+xml",
                        body='<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">'
                        '<rect width="200" height="200" fill="#b42d65"/></svg>',
                    ),
                )
                page = context.new_page()
                context.route(
                    "https://example.test/missing.svg", lambda route: route.fulfill(status=404)
                )
                errors = []
                page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                page.goto(base + "/?station_ids=1,3")
                expect(page.locator(".player-copy")).to_be_disabled()
                expect(page.locator("#chart-podium article")).to_have_count(2)
                expect(page.locator("#chart-subtitle")).to_contain_text("Radio 0, Radio 2")
                Path("/tmp/radio-mobile-redesign").mkdir(exist_ok=True)
                for theme in ("light", "dark"):
                    page.evaluate("(theme)=>document.documentElement.dataset.theme=theme", theme)
                    page.screenshot(
                        path=f"/tmp/radio-mobile-redesign/home-{width}-{theme}.png", full_page=True
                    )
                if width <= 760:
                    assert not page.locator("#chart-controls").evaluate("(e)=>e.open")
                    cards = page.locator("#chart-podium article")
                    first, second = cards.nth(0).bounding_box(), cards.nth(1).bounding_box()
                    assert second["y"] >= first["y"] + first["height"]
                    assert cards.first.locator(".cover").bounding_box()["width"] > width * 0.8
                    page.locator("#chart-controls > summary").click()
                page.locator("[data-open-export]").click()
                expect(page.locator("dialog [data-export-radio]:checked")).to_have_count(2)
                page.locator('dialog [name="format"]').select_option("json")
                with page.expect_download() as downloaded:
                    page.locator('dialog [type="submit"]').click()
                data = json.loads(Path(downloaded.value.path()).read_text())
                assert [r["title"] for r in data["items"]] == ["Song 2", "Song 0"]
                page.locator("[data-export-close]").click()
                page.locator(".station-selection summary").first.click()
                page.locator('[data-chart-radio][value="1"]').uncheck()
                expect(page.locator("#chart-podium article")).to_have_count(1)
                assert "station_id=3" in page.url
                page.locator("[data-chart-all]").click()
                expect(page.locator("#chart-podium article")).to_have_count(3)
                page.locator('[data-chart-radio][value="2"]').check()
                expect(page.locator("#chart-podium article")).to_have_count(1)
                expect(page.locator("#chart-podium")).to_contain_text("Song 1", timeout=20000)
                page.reload()
                expect(page.locator("#chart-podium")).to_contain_text("Song 1", timeout=20000)
                expect(page.locator(".player-copy")).to_be_enabled()
                page.locator(".player-copy").click()
                expect(page.locator(".catalog-dialog")).to_be_visible()
                expect(page.locator(".catalog-title")).to_have_text("Song 1")
                page.locator(".catalog-close").click()
                assert page.locator(".sidebar-bottom").count() == 0
                if width <= 760:
                    expect(page.locator(".sidebar")).not_to_be_visible()
                    page.locator("#mobile-nav-toggle").click()
                    expect(page.locator("#mobile-navigation")).to_be_visible()
                    assert page.locator("#mobile-navigation").bounding_box()["x"] == 0
                    page.keyboard.press("Escape")
                    expect(page.locator("#mobile-nav-toggle")).to_be_focused()
                    page.locator("#mobile-nav-toggle").click()
                page.locator('.sidebar a[href="/recherche"]').click()
                expect(page.locator("#results .search-artwork").first).to_be_visible()
                expect(page.locator("#results .search-artwork")).to_have_count(3)
                expect(page.locator("#results .search-artwork img")).to_have_count(1)
                if width <= 760:
                    expect(page.locator("#mobile-navigation")).not_to_be_visible()
                page.locator("#results .search-artwork").first.click()
                expect(page.locator(".catalog-dialog")).to_be_visible()
                page.locator(".catalog-close").click()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                expect(page.locator("#result-view")).to_have_value("tracks")
                expect(page.locator("#results tbody tr")).to_have_count(3)
                assert page.locator(".view-tabs").count() == 0
                if width <= 760:
                    expect(page.locator("#results th")).to_have_count(2)
                    assert page.locator("#results th").all_text_contents() == [
                        "Musique",
                        "Diffusions",
                    ]
                    assert page.locator("#results").evaluate("e=>e.scrollWidth<=e.clientWidth")
                page.locator(".result-columns > summary").click()
                page.locator('[data-column="artist"][type="checkbox"]').set_checked(width <= 760)
                count = 3 if width <= 760 else 5
                expect(page.locator("#results th")).to_have_count(count)
                page.reload()
                expect(page.locator("#results th")).to_have_count(count)
                page.locator(".result-columns > summary").click()
                page.locator("#reset-columns").click()
                expect(page.locator("#results th")).to_have_count(2 if width <= 760 else 6)
                page.locator(".result-columns > summary").click()
                page.locator("#result-view").select_option("plays")
                expect(page.locator("#results tbody tr")).to_have_count(6)
                if width <= 760:
                    assert page.locator("#results th").all_text_contents() == [
                        "Musique",
                        "Diffusion",
                    ]
                page.locator(".result-columns > summary").click()
                page.locator('[data-column="source"][type="checkbox"]').check()
                expect(page.locator('#results th[data-column="source"]')).to_be_visible()
                page.locator(".result-columns > summary").click()
                page.locator(".search-filters > summary").click()
                page.locator('#explorer-form [name="station_id"]').select_option("2")
                expect(page.locator("#results tbody tr")).to_have_count(2)
                expect(page.locator("#results")).to_contain_text("Song 1")
                page.locator('#explorer-form [name="q"]').fill("absent")
                expect(page.locator("#results")).to_contain_text("Aucun résultat")
                page.locator('#explorer-form [name="q"]').fill("")
                expect(page.locator("#results tbody tr")).to_have_count(2)
                page.get_by_role("button", name="Réinitialiser", exact=True).click()
                expect(page.locator("#result-view")).to_have_value("tracks")
                expect(page.locator("#results tbody tr")).to_have_count(3)
                page.locator(".search-filters > summary").click()
                assert (
                    page.locator("#results").evaluate("e=>e.scrollWidth<=e.clientWidth")
                    if width <= 760
                    else True
                )
                Path("/tmp/radio-station-selection").mkdir(exist_ok=True)
                for theme in ("light", "dark"):
                    page.evaluate("t=>document.documentElement.dataset.theme=t", theme)
                    page.screenshot(
                        path=f"/tmp/radio-station-selection/{width}-{theme}.png", full_page=True
                    )
                assert not errors, errors
                context.close()
            browser.close()
        print("Station sets: rankings, export, single/all, reload and mobile verified.")
    finally:
        server.terminate()

        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
