"""Manual isolated UI check: PYTHONPATH=. python tests/browser_broadcast_audit.py."""

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

CAPTURES = Path("/tmp/radio-broadcast-audit")
CAPTURES.mkdir(exist_ok=True)

with tempfile.TemporaryDirectory(prefix="radio-audit-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/test.db"
    os.environ["ADMIN_TOKEN"] = "browser-test-only"
    from app.database import SessionLocal, engine
    from app.models import Base, Play, Station
    from app.services.catalog import ensure_entry

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        a = Station(name="Radio Alpha", slug="alpha", stream_url="https://example.test/live")
        b = Station(name="Radio Beta", slug="beta", stream_url="https://example.test/live")
        db.add_all([a, b])
        db.flush()
        alpha, beta = a.id, b.id
        for radio, title, seconds, start in [
            (a, "Morceau Alpha", 0, 1000),
            (a, "Morceau Alpha", 100, 1000),
            (a, "Morceau Alpha", 800, 2000),
            (b, "Morceau Beta", 0, None),
            (b, "Interlude", 20, None),
            (b, "Morceau Beta", 200, None),
        ]:
            entry = ensure_entry(db, "Artiste Test", title)
            entry.selected = {"duration": 240}
            at = datetime.now(UTC) - timedelta(hours=1) + timedelta(seconds=seconds)
            db.add(
                Play(
                    station_id=radio.id,
                    artist="Artiste Test",
                    title=title,
                    source="json",
                    raw_metadata=json.dumps({"start": start}),
                    detected_at=at,
                    last_seen_at=at,
                )
            )
        db.commit()
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with open(f"{directory}/server.log", "w+") as log:
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
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
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get("CHROME_EXECUTABLE"))
                try:
                    for width in (1280, 390, 320):
                        context = browser.new_context(
                            viewport={"width": width, "height": 900}, locale="fr-FR"
                        )
                        context.route("https://**", lambda route: route.abort())
                        page = context.new_page()
                        errors = []
                        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                        page.goto(base + "/admin/diffusions")
                        page.locator("#admin-login input").fill("browser-test-only")
                        page.locator("#admin-login button").click()
                        form = page.locator("#admin-audit-form")
                        results = page.locator("#admin-audit-results")
                        message = page.locator("#admin-audit-status")
                        expect(form.locator('select[name="station_id"] option')).to_have_count(3)
                        form.get_by_role("button", name="Analyser l’historique").click()
                        expect(message).to_contain_text(
                            "6 passage(s) contrôlé(s) · 3 signalement(s)"
                        )
                        expect(results.locator("article")).to_have_count(3)
                        expect(page.locator("#admin-audit-summary")).to_contain_text("Radio Alpha")
                        expect(page.locator("#admin-audit-summary")).to_contain_text("Radio Beta")
                        for dark in (False, True):
                            page.evaluate(
                                "dark => document.documentElement.dataset.theme = "
                                "dark ? 'dark' : 'light'",
                                dark,
                            )
                            page.screenshot(
                                path=str(
                                    CAPTURES / f"audit-{width}-{'dark' if dark else 'light'}.png"
                                ),
                                full_page=True,
                            )
                            assert page.evaluate(
                                "document.documentElement.scrollWidth <= innerWidth"
                            )
                        form.locator('[name="reason"]').select_option("source")
                        expect(results).to_be_empty()
                        form.get_by_role("button").click()
                        expect(results.locator("article")).to_have_count(1)
                        results.get_by_role("button", name="Préparer le regroupement").click()
                        correction = page.locator("#admin-play-correction")
                        expect(correction.locator('[name="action"]')).to_have_value("merge")
                        correction.get_by_role("button", name="Prévisualiser").click()
                        expect(page.locator("#admin-play-preview")).to_contain_text("Avant")
                        page.get_by_role("button", name="Annuler", exact=True).click()
                        expect(page.locator("#admin-play-preview")).to_be_empty()
                        # Personal exclusions refresh audit counters during the admin session.
                        page.evaluate(
                            "() => window.MusicFilters.hide("
                            "{artist: 'Artiste Test', title: 'Morceau Alpha'}, false)"
                        )
                        expect(message).to_contain_text(
                            "3 passage(s) contrôlé(s) · 0 signalement(s)"
                        )
                        # New browser context restores the full history without personal exclusions.
                        if width == 320:
                            context.close()
                            context = browser.new_context(viewport={"width": width, "height": 900})
                            page = context.new_page()
                            page.goto(base + "/admin/diffusions")
                            page.locator("#admin-login input").fill("browser-test-only")
                            page.locator("#admin-login button").click()
                            form = page.locator("#admin-audit-form")
                            form.locator('[name="reason"]').select_option("source")
                            form.get_by_role("button").click()
                            results = page.locator("#admin-audit-results")
                            expect(results.locator("article")).to_have_count(1)
                            results.get_by_role("button", name="Préparer le regroupement").click()
                            page.locator("#admin-play-correction button").click()
                            page.get_by_role("button", name="Confirmer ces modifications").click()
                            expect(page.locator("#admin-plays-status")).to_have_text(
                                "Modifications enregistrées."
                            )
                            expect(page.locator("#admin-audit-status")).to_contain_text(
                                "5 passage(s) contrôlé(s) · 0 signalement(s)"
                            )
                            with SessionLocal() as db:
                                assert db.query(Play).count() == 5
                                assert (
                                    db.query(Play).filter_by(raw_metadata='{"start": 2000}').count()
                                    == 1
                                )
                        assert not errors, errors
                        context.close()
                finally:
                    browser.close()
            print(
                "Audit UI: all stations, duration, filters, preview/cancel/merge, replay preserved."
            )
        except Exception:
            log.seek(0)
            print(log.read()[-5000:])
            raise
        finally:
            server.terminate()
            server.wait(timeout=10)
