"""Exercise global and personal hiding in two isolated browser contexts, using temporary data."""

import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="radio-global-browser-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/browser.db"
    os.environ["ADMIN_TOKEN"] = "browser-test-only"
    from app.database import SessionLocal, engine
    from app.models import Base, Play, Station
    from app.services.catalog import ensure_entry

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        station = Station(name="Test Radio", slug="test", stream_url="https://example.com/live")
        db.add(station)
        db.flush()
        for count, title in [(5, "Global"), (4, "Personal"), (3, "Keep")]:
            ensure_entry(db, "Artist", title)
            for index in range(count):
                at = datetime.now(UTC) - timedelta(minutes=index + 1)
                db.add(
                    Play(
                        station_id=station.id,
                        artist="Artist",
                        title=title,
                        raw_metadata=title,
                        detected_at=at,
                        last_seen_at=at,
                        source="icy",
                    )
                )
        db.commit()
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with open(f"{directory}/server.log", "w") as log:
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
                browser = p.chromium.launch()
                admin_context = browser.new_context()
                visitor_context = browser.new_context()
                admin = admin_context.new_page()
                visitor = visitor_context.new_page()
                errors = []
                for page in (admin, visitor):
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.route("https://**", lambda route: route.abort())
                # Existing personal exclusions remain personal after login.
                admin_context.add_init_script("""localStorage.setItem('radio-hidden-tracks-v1',
                    JSON.stringify([{artist:'Artist', title:'Personal'}]));""")
                visitor.goto(base)
                expect(visitor.locator(".podium-card")).to_have_count(3)
                visitor.locator(".podium-detail").filter(has_text="Personal").click()
                visitor.get_by_role("button", name="Masquer pour moi", exact=True).click()
                expect(visitor.locator(".podium-card")).to_have_count(2)
                admin.goto(base + "/admin")
                admin.locator('[name="token"]').fill("browser-test-only")
                admin.get_by_role("button", name="Ouvrir l’administration").click()
                expect(admin.locator("#admin-workspace")).to_be_visible()
                assert admin.request.get(base + "/api/admin/exclusions").json()["items"] == []
                admin.locator(".sidebar nav").get_by_role("link", name="Classements").click()
                expect(admin.locator(".podium-card")).to_have_count(2)
                admin.locator(".podium-detail").filter(has_text="Global").click()
                admin.get_by_role("button", name="Masquer pour tous", exact=True).click()
                expect(admin.locator(".catalog-dialog")).not_to_be_visible()
                expect(admin.locator(".podium-card")).to_have_count(1)
                visitor.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(visitor.locator(".podium-card")).to_have_count(1)
                expect(visitor.locator(".podium-detail")).to_contain_text("Keep")
                assert admin.evaluate(
                    "JSON.parse(localStorage.getItem('radio-hidden-tracks-v1'))"
                ) == [{"artist": "Artist", "title": "Personal"}]
                # Restoring a global exclusion keeps each browser's personal exclusion.
                admin.locator("[data-open-exclusions]").click()
                expect(admin.locator(".exclusions-dialog")).to_contain_text(
                    "Mes exclusions personnelles"
                )
                admin.get_by_role("button", name="Réafficher pour tous").click()
                expect(admin.locator(".exclusions-dialog")).to_contain_text(
                    "Aucun titre masqué pour tous"
                )
                visitor.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(visitor.locator(".podium-card")).to_have_count(2)
                expect(visitor.locator(".podium")).not_to_contain_text("Personal")
                # Manual entries use the same global list, and errors keep the dialog open.
                panel = admin.locator(".exclusions-dialog")
                panel.locator("input").first.fill("Keep")
                panel.locator("input").nth(1).fill("Artist")
                admin.route(
                    "**/api/admin/exclusions",
                    lambda route: (
                        route.fulfill(status=503, json={"detail": "Enregistrement indisponible"})
                        if route.request.method == "POST"
                        else route.continue_()
                    ),
                )
                panel.get_by_role("button", name="Masquer pour tous", exact=True).click()
                expect(panel.locator('[role="status"]')).to_have_text("Enregistrement indisponible")
                expect(panel).to_be_visible()
                assert not admin.request.get(base + "/api/admin/exclusions").json()["items"]
                admin.unroute("**/api/admin/exclusions")
                panel.get_by_role("button", name="Masquer pour tous", exact=True).click()
                expect(panel.locator('[role="status"]')).to_have_text("Filtre enregistré.")
                visitor.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(visitor.locator(".podium-card")).to_have_count(1)
                expect(visitor.locator(".podium-detail")).to_contain_text("Global")
                # Expiring the session cannot silently turn a global action into a personal one.
                panel.locator("input").first.fill("Global")
                panel.locator("input").nth(1).fill("Artist")
                csrf = admin.request.get(base + "/api/admin/session").json()["csrf_token"]
                response = admin.request.post(
                    base + "/api/admin/logout",
                    data={},
                    headers={"Origin": base, "X-CSRF-Token": csrf},
                )
                assert response.ok
                panel.get_by_role("button", name="Masquer pour tous", exact=True).click()
                expect(panel).to_be_visible()
                expect(panel.locator('[role="status"]')).to_contain_text("Session administrateur")
                assert admin.evaluate(
                    "JSON.parse(localStorage.getItem('radio-hidden-tracks-v1'))"
                ) == [{"artist": "Artist", "title": "Personal"}]
                visitor.reload()
                expect(visitor.locator(".podium-detail")).to_contain_text("Global")
                expect(visitor.locator(".podium-card")).to_have_count(1)
                # Phase 2: statistics retain personal filters across sessions, and bulk
                # promotion affects a visitor who has no personal preferences.
                admin.goto(base + "/admin")
                admin.locator('[name="token"]').fill("browser-test-only")
                admin.get_by_role("button", name="Ouvrir l’administration").click()
                expect(admin.locator("#admin-workspace")).to_be_visible()
                admin.locator(".sidebar nav").get_by_role("link", name="Stats", exact=True).click()
                genre_status = '[data-metric="genres"] [role="status"]'
                expect(admin.locator(genre_status)).to_contain_text("5 passages")
                expect(
                    admin.locator('[data-metric="repetitions"] .metric-output')
                ).not_to_contain_text("Personal")
                fresh_context = browser.new_context(viewport={"width": 402, "height": 874})
                fresh = fresh_context.new_page()
                fresh.on("pageerror", lambda error: errors.append(str(error)))
                fresh.route("https://**", lambda route: route.abort())
                fresh.goto(base + "/stats")
                expect(fresh.locator(genre_status)).to_contain_text("9 passages")
                admin.locator("[data-open-exclusions]").click()
                panel = admin.locator(".exclusions-dialog")
                expect(panel).not_to_contain_text("Hit Music Only")
                panel.get_by_role(
                    "button", name="Appliquer mes 1 exclusions personnelles à tous"
                ).click()
                expect(panel.locator('[role="status"]')).to_have_text(
                    "Exclusions appliquées à tous. Votre liste personnelle est conservée."
                )
                fresh.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(fresh.locator(genre_status)).to_contain_text("5 passages")
                expect(
                    fresh.locator('[data-metric="repetitions"] .metric-output')
                ).not_to_contain_text("Personal")
                # Restoring globally leaves the admin's personal list in force.
                panel.locator(".hidden-track").filter(has_text="Personal").get_by_role(
                    "button", name="Réafficher pour tous"
                ).click()
                fresh.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(fresh.locator(genre_status)).to_contain_text("9 passages")
                expect(admin.locator(genre_status)).to_contain_text("5 passages")
                panel.get_by_role("button", name="Fermer", exact=True).click()
                admin.locator("[data-admin-logout]").click()
                expect(admin.locator("#admin-session-controls")).not_to_be_visible()
                expect(admin.locator(genre_status)).to_contain_text("5 passages")
                admin.reload()
                expect(admin.locator(genre_status)).to_contain_text("5 passages")
                assert admin.evaluate(
                    "JSON.parse(localStorage.getItem('radio-hidden-tracks-v1'))"
                ) == [{"artist": "Artist", "title": "Personal"}]
                assert fresh.evaluate("document.documentElement.scrollWidth <= innerWidth")
                fresh_context.close()
                assert not errors, errors
                browser.close()
                print(
                    "Two-browser global hiding, personal preferences, restoration and failures: OK"
                )
        except Exception:
            print(Path(f"{directory}/server.log").read_text())
            raise
        finally:
            server.terminate()
            server.wait(timeout=10)
    engine.dispose()
