"""Phase 5: export filters, selection and downloads on desktop and mobile."""

import csv
import io
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

with tempfile.TemporaryDirectory(prefix="radio-exports-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/test.db"
    os.environ["ADMIN_TOKEN"] = "browser-test-only"
    from app.database import SessionLocal, engine
    from app.models import Base, Play, Station
    from app.services.catalog import ensure_entry

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        station = Station(
            name="Export Radio", slug="export", stream_url="https://example.test/live"
        )
        other = Station(name="Other Radio", slug="other", stream_url="https://example.test/other")
        db.add_all([station, other])
        db.flush()
        station_id = station.id
        at = datetime.now(UTC)
        for i in range(14):
            for j in range(15 - i):
                db.add(
                    Play(
                        station_id=station.id,
                        artist="Artist",
                        title=f"Song {i:02}",
                        source="icy",
                        raw_metadata="original",
                        detected_at=at - timedelta(minutes=j),
                        last_seen_at=at,
                    )
                )
            entry = ensure_entry(db, "Artist", f"Song {i:02}")
            entry.selected = {
                "provider": "itunes",
                "provider_id": str(i),
                "album": "Album",
                "genres": ["Pop", "Dance"],
                "release_date": "2026-01-01",
            }
            entry.lyrics = {"plain": "Paroles de test"}
        db.add(
            Play(
                station_id=other.id,
                artist="Other",
                title="Other song",
                source="icy",
                raw_metadata="other",
                detected_at=at,
                last_seen_at=at,
            )
        )
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
                    for width in (1280, 390):
                        context = browser.new_context(viewport={"width": width, "height": 900})
                        page = context.new_page()
                        errors = []
                        page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                        base = f"http://127.0.0.1:{port}"
                        page.goto(f"{base}/?station_id={station_id}&days=all")
                        expect(page.locator("#chart-status")).to_contain_text("Top")
                        assert page.locator("[data-export-format]").count() == 0
                        # Apply the global blacklist through the real admin route.
                        response = page.request.post(
                            f"{base}/api/admin/exclusions",
                            headers={"Authorization": "Bearer browser-test-only"},
                            data={"artist": "Artist", "title": "Song 00"},
                        )
                        assert response.ok
                        page.evaluate("""async () => {
                            await window.MusicFilters.refresh();
                            await window.MusicFilters.hide(
                                {artist: 'Artist', title: 'Song 01'}, false);
                        }""")
                        page.locator("[data-open-export]").click()
                        dialog = page.locator(".export-dialog")
                        expect(dialog).to_be_visible()
                        expect(dialog.locator("[type=submit]")).to_be_enabled()
                        expect(dialog.locator("[name=station_id]")).to_have_value(str(station_id))
                        expect(dialog.locator("[name=view]")).to_have_value("tracks")
                        dialog.locator("[name=limit]").fill("10")
                        dialog.locator("[name=format]").select_option("json")

                        def download_json(page=page, dialog=dialog):
                            with page.expect_download() as download:
                                dialog.locator("[type=submit]").click()
                            return json.loads(Path(download.value.path()).read_text())

                        def verify_top(download_json=download_json):
                            result = download_json()
                            assert result["count"] == 10
                            assert [r["title"] for r in result["items"]] == [
                                f"Song {i:02}" for i in range(2, 12)
                            ]
                            assert all(set(r) == {"title", "artist"} for r in result["items"])

                        verify_top()
                        # The same personal exclusion applies during and after an admin session.
                        page.evaluate("""async () => {
                            await window.RadioAdmin.login('browser-test-only');
                            await window.RadioAdmin.refresh();
                        }""")
                        verify_top()
                        page.evaluate("""async () => {
                            await window.RadioAdmin.logout();
                            await window.RadioAdmin.refresh();
                        }""")
                        verify_top()
                        dialog.locator("[name=format]").select_option("csv")
                        with page.expect_download() as download:
                            dialog.locator("[type=submit]").click()
                        rows = list(
                            csv.DictReader(
                                io.StringIO(
                                    Path(download.value.path()).read_text(encoding="utf-8-sig")
                                ),
                                delimiter=";",
                            )
                        )
                        assert len(rows) == 10 and set(rows[0]) == {"artist", "title"}
                        dialog.locator("[data-fields=all]").click()
                        dialog.locator("[name=limit]").fill("")
                        dialog.locator("[name=format]").select_option("json")
                        result = download_json()
                        assert result["count"] == 12
                        assert result["items"][0]["metadata"]["release_date"] == "2026-01-01"
                        assert result["items"][0]["lyrics"]["plain"] == "Paroles de test"
                        dialog.locator("[name=view]").select_option("plays")
                        expect(dialog.locator("[name=min_plays]")).to_be_disabled()
                        assert dialog.locator("input[value=station_name]").count() == 1
                        dialog.locator("[data-fields=minimal]").click()
                        dialog.locator("[name=title]").fill("No match")
                        assert download_json()["items"] == []
                        dialog.locator("[data-fields=minimal]").click()
                        dialog.locator("input[value=artist]").uncheck()
                        dialog.locator("input[value=title]").uncheck()
                        dialog.locator("[type=submit]").click()
                        expect(dialog.locator("[data-export-status]")).to_have_text(
                            "Choisissez au moins un champ."
                        )
                        page.evaluate("document.documentElement.dataset.theme = 'dark'")
                        assert dialog.evaluate("e => e.scrollWidth <= e.clientWidth")
                        page.screenshot(path=f"/tmp/track-radio-export-{width}.png")
                        dialog.locator("[data-export-close]").click()
                        # Internal navigation keeps the common export flow functional.
                        page.locator('a.nav-item[href="/recherche"]').click()
                        expect(page.locator("h1")).to_have_text("Recherche avancée")
                        page.locator("#explorer-form [name=artist]").fill("Artist")
                        page.wait_for_function(
                            "() => new URLSearchParams(location.search).get('artist') === 'Artist'"
                        )
                        page.locator("[data-open-export]").click()
                        expect(page.locator(".export-dialog [name=artist]")).to_have_value("Artist")
                        assert not errors, errors
                        context.close()
                        print(f"Export browser OK: {width}px")
                finally:
                    browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)
