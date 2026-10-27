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

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        for i in range(3):
            station = Station(
                name=f"Radio {i}", slug=f"radio-{i}", stream_url="https://example.test"
            )
            db.add(station)
            db.flush()
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
                context = browser.new_context(viewport={"width": width, "height": 846})
                context.route("**/api/stations/*/artwork", lambda route: route.fulfill(status=404))
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                page.goto(base + "/?station_ids=1,3")
                expect(page.locator("#chart-podium article")).to_have_count(2)
                expect(page.locator("#chart-subtitle")).to_contain_text("Radio 0, Radio 2")
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
                page.locator("#chart-station").select_option("2")
                expect(page.locator("#chart-podium article")).to_have_count(1)
                expect(page.locator("#chart-podium")).to_contain_text("Song 1", timeout=20000)
                page.reload()
                expect(page.locator("#chart-podium")).to_contain_text("Song 1", timeout=20000)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                Path("/tmp/radio-station-selection").mkdir(exist_ok=True)
                page.screenshot(path=f"/tmp/radio-station-selection/{width}.png", full_page=True)
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
