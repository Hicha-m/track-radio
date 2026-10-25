"""Run manually: PYTHONPATH=. .venv/bin/python tests/browser_data_admin.py."""

import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta

from playwright.sync_api import expect, sync_playwright

with tempfile.TemporaryDirectory(prefix="radio-data-admin-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/test.db"
    os.environ["ADMIN_TOKEN"] = "browser-test-only"
    from app.database import SessionLocal, engine
    from app.models import Base, Play, Station
    from app.services.catalog import ensure_entry

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        station = Station(name="Mouv'", slug="mouv", stream_url="https://example.test/live")
        db.add(station)
        db.flush()
        start = datetime.now(UTC) - timedelta(hours=1)
        for seconds in (0, 42, 600):
            at = start + timedelta(seconds=seconds)
            db.add(
                Play(
                    station_id=station.id,
                    artist="A2H",
                    title="Ossau-Iraty",
                    source="json",
                    raw_metadata='{"start": null, "payload": {}}',
                    detected_at=at,
                    last_seen_at=at,
                )
            )
        ensure_entry(db, "A2H", "Ossau-Iraty")
        db.commit()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with open(f"{directory}/server.log", "w+") as log:
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
            stdout=log,
            stderr=log,
            env=os.environ,
        )
        try:
            for _ in range(100):
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.1)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    for width in (1280, 402):
                        context = browser.new_context(viewport={"width": width, "height": 900})
                        page = context.new_page()
                        errors = []
                        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                        page.goto(f"http://127.0.0.1:{port}/admin")
                        page.locator("#admin-login input").fill("browser-test-only")
                        page.locator("#admin-login button").click()
                        expect(page.locator("#admin-workspace")).to_be_visible()
                        expect(page.locator("#admin-plays-results article")).to_have_count(3)
                        page.locator('#admin-plays-form input[name="suspicious"]').check()
                        page.locator("#admin-plays-form button").click()
                        expect(page.locator("#admin-plays-results article")).to_have_count(1)
                        expect(page.locator("#admin-plays-results")).to_contain_text(
                            "Passage rapproché"
                        )
                        page.locator("#admin-plays-results input").check()
                        page.locator("#admin-play-correction button").click()
                        expect(page.locator("#admin-play-preview")).to_contain_text("Avant")
                        expect(page.locator("#admin-play-preview")).to_contain_text("Après")
                        expect(page.locator("#admin-play-preview")).to_contain_text("supprimées")
                        if width == 1280:
                            page.get_by_role("button", name="Annuler", exact=True).click()
                            expect(page.locator("#admin-play-preview")).to_be_empty()
                        else:
                            page.get_by_role("button", name="Confirmer ces modifications").click()
                            expect(page.locator("#admin-plays-status")).to_have_text(
                                "Modifications enregistrées."
                            )
                            expect(page.locator("#admin-plays-results article")).to_have_count(0)
                            page.locator('#admin-plays-form input[name="suspicious"]').uncheck()
                            page.locator("#admin-plays-form button").click()
                            expect(page.locator("#admin-plays-results article")).to_have_count(2)
                            with SessionLocal() as db:
                                assert db.query(Play).count() == 2
                        assert not errors, errors
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                        if width == 402:
                            page.screenshot(path="/tmp/radio-phase1-mobile.png", full_page=True)
                        context.close()
                finally:
                    browser.close()
            print("Administration desktop/mobile: search, preview, cancel, apply verified.")
        except Exception:
            log.seek(0)
            print(log.read()[-5000:])
            raise
        finally:
            server.terminate()
            server.wait(timeout=10)
