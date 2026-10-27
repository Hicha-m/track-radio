"""Local visual and navigation regression: PYTHONPATH=. python tests/browser_admin_layout.py."""

import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
CAPTURES = Path("/tmp/radio-admin-layout")
CAPTURES.mkdir(exist_ok=True)

with tempfile.TemporaryDirectory(prefix="radio-admin-layout-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/test.db"
    os.environ["ADMIN_TOKEN"] = "browser-test-only"
    from app.database import SessionLocal, engine
    from app.models import Base, Play, Station
    from app.services.catalog import ensure_entry

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        stations = [
            Station(
                name=name,
                slug=f"station-{i}",
                stream_url="https://example.test/live",
                enabled=i != 2,
            )
            for i, name in enumerate(("Radio Test", "Station Pop", "Station Électro"))
        ]
        db.add_all(stations)
        db.flush()
        for i, title in enumerate(
            ("Un titre à vérifier", "Morceau validé", "Exclusion personnelle")
        ):
            entry = ensure_entry(db, "Artiste Test", title)
            entry.status = "error" if i == 0 else "matched"
            at = datetime.now(UTC) - timedelta(minutes=i * 10)
            db.add(
                Play(
                    station_id=stations[0].id,
                    title=title,
                    artist="Artiste Test",
                    source="icy",
                    raw_metadata=title,
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
            cwd=ROOT,
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
                browser = p.chromium.launch()
                try:
                    for width in (1440, 1024, 390, 320):
                        context = browser.new_context(
                            viewport={"width": width, "height": 900}, locale="fr-FR"
                        )
                        context.route("https://**", lambda route: route.abort())
                        page = context.new_page()
                        errors = []
                        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                        page.goto(base + "/admin/radios")
                        expect(page).to_have_url(base + "/admin?next=/admin/radios")
                        expect(page.locator("#admin-workspace")).to_have_count(0)
                        page.screenshot(
                            path=str(CAPTURES / f"login-light-{width}.png"), full_page=True
                        )
                        if width == 1440:
                            page.locator("#admin-login input").fill("wrong-test-key")
                            page.locator("#admin-login button").click()
                            expect(page.locator("#admin-status")).to_have_text(
                                "Clé administrateur invalide."
                            )
                            expect(page.locator("#admin-login input")).to_have_value("")
                        page.locator("#admin-login input").fill("browser-test-only")
                        page.locator("#admin-login button").click()
                        expect(page).to_have_url(base + "/admin/radios")
                        expect(page.locator("#admin-stations article")).to_have_count(3)
                        expect(page.locator("#admin-login")).to_have_count(0)
                        page.evaluate("window.retainedAdminAudio = document.querySelector('audio')")
                        admin_nav = page.get_by_role(
                            "navigation", name="Administration", exact=True
                        )

                        def check_layout(name, theme, page=page, width=width):
                            assert page.evaluate(
                                "document.documentElement.scrollWidth <= innerWidth"
                            ), (width, name, "page overflow")
                            assert page.locator("#player-toggle").evaluate("""button => {
                                const a = button.getBoundingClientRect();
                                const b = button.querySelector('svg').getBoundingClientRect();
                                return Math.abs((a.left+a.right-b.left-b.right)/2) < 1
                                    && Math.abs((a.top+a.bottom-b.top-b.bottom)/2) < 1;
                            }"""), (width, "player icon off centre")
                            assert page.evaluate(
                                "document.querySelector('audio') === retainedAdminAudio"
                            )
                            expect(page.locator(".quick-search-toggle")).to_have_count(0)
                            expect(
                                page.get_by_role("link", name="Historique des diffusions")
                            ).to_have_count(0)
                            page.screenshot(
                                path=str(CAPTURES / f"{name}-{theme}-{width}.png"), full_page=True
                            )

                        # Personal exclusions must remain effective throughout the admin session.
                        page.evaluate("""localStorage.setItem('radio-hidden-tracks-v1',
                            JSON.stringify([
                            {artist:'Artiste Test', title:'Exclusion personnelle'}]))""")
                        page.reload()
                        page.evaluate("window.retainedAdminAudio = document.querySelector('audio')")
                        for theme in ("light", "dark"):
                            if theme == "dark":
                                page.locator("#theme-toggle").click()
                            for path, label, target in (
                                ("dashboard", "Tableau de bord", ".admin-metrics"),
                                ("radios", "Radios", "#admin-stations article"),
                                ("diffusions", "Diffusions", "#admin-plays-results article"),
                                ("catalogue", "Catalogue", ".admin-track"),
                                ("exclusions", "Exclusions", "#admin-exclusion-results p"),
                            ):
                                admin_nav.get_by_role("link", name=label, exact=True).click()
                                expect(page).to_have_url(base + "/admin/" + path)
                                expect(page.locator(target).first).to_be_visible()
                                expect(
                                    admin_nav.get_by_role("link", name=label, exact=True)
                                ).to_have_attribute("aria-current", "page")
                                if path == "dashboard":
                                    expect(
                                        page.locator('[data-admin-metric="recent_plays"]')
                                    ).to_have_text("2")
                                    expect(
                                        page.locator('[data-admin-metric="visible_tracks"]')
                                    ).to_have_text("2")
                                    expect(
                                        page.locator('[data-admin-metric="review_tracks"]')
                                    ).to_have_text("1")
                                check_layout(path, theme)
                        # Browser history and direct reload preserve the route and the session.
                        page.go_back()
                        expect(page).to_have_url(base + "/admin/catalogue")
                        expect(page.locator(".admin-track")).to_have_count(3)
                        page.reload()
                        expect(page.locator(".admin-track")).to_have_count(3)
                        page.evaluate("window.retainedAdminAudio = document.querySelector('audio')")
                        admin_nav.get_by_role("link", name="Tableau de bord", exact=True).click()
                        page.locator('a[href="/admin/catalogue?review=true"]').click()
                        expect(page.locator('#admin-catalog-form [name="review"]')).to_be_checked()
                        expect(page.locator(".admin-track")).to_have_count(1)
                        # Failed summary refresh is explicit and can be retried.
                        admin_nav.get_by_role("link", name="Tableau de bord", exact=True).click()
                        expect(page.locator(".admin-overview")).to_have_attribute(
                            "aria-busy", "false"
                        )
                        page.route(
                            "**/api/admin/dashboard?**",
                            lambda route: route.fulfill(
                                status=503, json={"detail": "Synthèse temporairement indisponible."}
                            ),
                        )
                        page.get_by_role("button", name="Actualiser", exact=True).click()
                        expect(page.locator("#admin-status")).to_contain_text("indisponible")
                        page.unroute("**/api/admin/dashboard?**")
                        page.get_by_role("button", name="Actualiser", exact=True).click()
                        expect(page.locator("#admin-status")).to_have_text("Synthèse actualisée.")
                        page.locator("#admin-session-controls [data-admin-logout]").click()
                        expect(page.locator("#admin-login")).to_be_visible()
                        page.go_back()
                        expect(page.locator("#admin-login")).to_be_visible()
                        expect(page.locator("#admin-workspace")).to_have_count(0)
                        # The remaining search entry works on touch-sized screens too.
                        page.get_by_role("link", name="Revenir aux classements").click()
                        expect(page.locator("h1")).to_have_text("Classements")
                        expect(page.locator("#track-title")).to_have_text("Sélectionnez une radio")
                        if width <= 760:
                            page.locator("#mobile-nav-toggle").click()
                        page.locator(".search-shortcut").click()
                        expect(page.locator(".music-search-dialog")).to_be_visible()
                        page.keyboard.press("Escape")
                        check_layout("public", "dark")
                        assert not errors, errors
                        assert page.evaluate(
                            "!JSON.stringify({...localStorage,...sessionStorage}).includes('browser-test-only')"
                        )
                        print(
                            f"Admin {width}px: routes, sessions, filters, history, errors, "
                            "themes and player alignment passed.",
                            flush=True,
                        )
                        context.close()
                finally:
                    browser.close()
        except Exception:
            log.seek(0)
            print(log.read()[-4000:])
            raise
        finally:
            server.terminate()
            server.wait(timeout=10)
