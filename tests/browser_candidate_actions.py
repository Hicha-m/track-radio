"""Phase 3: real audio playback and persisted per-candidate completion on desktop/mobile."""

import io
import os
import socket
import subprocess
import sys
import tempfile
import time
import wave
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

PLAYING = """() => {
    const audio = document.querySelector('audio');
    return !audio.paused && audio.currentTime > 0;
}"""

with tempfile.TemporaryDirectory(prefix="radio-candidates-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/test.db"
    os.environ["ADMIN_TOKEN"] = "browser-test-only"
    from app.database import SessionLocal, engine
    from app.models import Base, Play, Station
    from app.services.catalog import ensure_entry

    Base.metadata.create_all(engine)
    selected = {
        "provider": "deezer",
        "provider_id": "42",
        "artist": "Quavo",
        "title": "Backwards (feat. T.I.)",
        "album": "Known Album",
        "genres": [],
        "isrc": None,
    }
    itunes = {
        "provider": "itunes",
        "provider_id": "192",
        "artist": "Quavo & T.I.",
        "title": "Backwards",
        "album": "Backwards - Single",
        "duration": 192,
        "genres": ["Hip-hop"],
        "genre_source": "itunes",
        "preview_url": "https://audio.example.test/itunes.wav",
        "cover_url": "https://images.example.test/cover.jpg",
        "url": "https://music.apple.com/track/192",
    }
    deezer = {
        **itunes,
        "provider": "deezer",
        "provider_id": "191",
        "preview_url": "https://audio.example.test/expired.mp3",
        "duration": 191,
    }
    with SessionLocal() as db:
        station = Station(name="Test Radio", slug="test", stream_url="https://example.test/live")
        db.add(station)
        db.flush()
        at = datetime.now(UTC)
        db.add(
            Play(
                station_id=station.id,
                artist="Quavo",
                title=selected["title"],
                detected_at=at,
                last_seen_at=at,
                source="icy",
                raw_metadata="original",
            )
        )
        entry = ensure_entry(db, "Quavo", selected["title"])
        key = entry.id
        db.commit()
    # Stub the provider, while the actual candidate-preview API and redirects run.
    Path(directory, "phase3_app.py").write_text("""from app.main import app as application
import app.catalog_api
async def fresh(*args, **kwargs):
    return {"preview": "https://audio.example.test/fresh.wav"}
app.catalog_api.fetch_json = fresh
""")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with open(f"{directory}/server.log", "w+") as log:
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "phase3_app:application",
                "--app-dir",
                directory,
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
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as sound:
                sound.setnchannels(1)
                sound.setsampwidth(2)
                sound.setframerate(8000)
                sound.writeframes(b"\x00\x00" * 8000 * 30)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    for width in (1280, 402):
                        with SessionLocal() as db:
                            entry = db.get(type(entry), key)
                            entry.selected = deepcopy(selected)
                            entry.candidates = [deezer, itunes, deepcopy(selected)]
                            entry.status = "matched"
                            entry.primary_genre = None
                            entry.revision += 1
                            entry.lyrics = {"plain": "Preserved lyrics"}
                            db.commit()
                        context = browser.new_context(viewport={"width": width, "height": 900})
                        page = context.new_page()
                        errors = []
                        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                        page.route(
                            "https://audio.example.test/**",
                            lambda route: route.fulfill(
                                status=410 if "expired" in route.request.url else 200,
                                content_type="audio/wav",
                                body=buffer.getvalue(),
                            ),
                        )
                        page.route(
                            "https://images.example.test/**",
                            lambda route: route.fulfill(status=404),
                        )

                        def excerpt(route):
                            # Check the real API redirect, then serve its media locally.
                            response = route.fetch(max_redirects=0)
                            assert response.status == 307, response.text()
                            assert response.headers["location"].endswith(
                                ("fresh.wav", "itunes.wav")
                            )
                            route.fulfill(content_type="audio/wav", body=buffer.getvalue())

                        page.route("**/api/catalog/**/preview", excerpt)
                        page.goto(f"http://127.0.0.1:{port}/admin")
                        page.locator("#admin-login input").fill("browser-test-only")
                        page.locator("#admin-login button").click()
                        expect(page.locator("#admin-workspace")).to_be_visible()
                        page.evaluate("(key) => window.RadioAdmin.openEditor({track_id:key})", key)
                        panel = page.locator(".admin-edit-dialog")
                        expect(panel).to_be_visible()
                        expect(
                            panel.locator(".catalog-editor > div > .catalog-genres")
                        ).to_contain_text("Aucun genre renseigné")
                        rows = panel.locator(".candidate")
                        expect(rows).to_have_count(3)
                        expect(rows.nth(1).locator(".catalog-genres li")).to_have_text(["Hip-hop"])
                        expect(
                            rows.nth(2).get_by_role(
                                "button", name="Compléter les informations manquantes"
                            )
                        ).to_have_count(0)
                        expect(panel.locator(".catalog-editor-actions button")).to_have_count(3)
                        assert "Genre principal" not in panel.inner_text()
                        rows.first.locator(".preview-button").click()
                        try:
                            page.wait_for_function(PLAYING, timeout=5000)
                        except Exception:
                            print(
                                page.evaluate("""() => {
                                const a = document.querySelector('audio');
                                return {paused:a.paused, time:a.currentTime, ready:a.readyState,
                                    error:a.error?.message, src:a.currentSrc,
                                    status:document.querySelector('#player-status').textContent};
                            }""")
                            )
                            raise
                        assert "/candidates/" in page.locator("#radio-player").get_attribute("src")
                        with SessionLocal() as db:
                            assert db.get(type(entry), key).selected["provider_id"] == "42"
                        rows.first.locator(".preview-button").click()
                        page.wait_for_function('document.querySelector("#radio-player").paused')
                        rows.nth(1).get_by_role(
                            "button", name="Compléter les informations manquantes"
                        ).click()
                        expect(panel.locator('[role="status"]').last).to_contain_text(
                            "Informations complétées"
                        )
                        expect(
                            panel.locator(".catalog-editor > div > .catalog-genres li")
                        ).to_have_text(["Hip-hop"])
                        expect(
                            panel.get_by_role(
                                "button", name="Compléter les informations manquantes"
                            )
                        ).to_have_count(0)
                        with SessionLocal() as db:
                            saved = db.get(type(entry), key)
                            assert (
                                saved.selected["provider_id"] == "42"
                                and saved.selected["album"] == "Known Album"
                            )
                            assert (
                                saved.selected["duration"] == 192
                                and saved.selected.get("isrc") is None
                            )
                            assert saved.lyrics == {"plain": "Preserved lyrics"}
                        rows.nth(1).get_by_role("button", name="Choisir cette version").click()
                        expect(panel.locator('[role="status"]').last).to_contain_text(
                            "Correction enregistrée"
                        )
                        panel.get_by_role("button", name="Fermer", exact=True).click()
                        page.evaluate(
                            '(title) => window.TrackCatalog.open({artist:"Quavo",title})',
                            selected["title"],
                        )
                        catalog = page.locator(".catalog-dialog")
                        expect(catalog.locator(".catalog-genres li")).to_have_text(["Hip-hop"])
                        catalog.locator(".preview-button").click()
                        try:
                            page.wait_for_function(PLAYING, timeout=5000)
                        except Exception:
                            print(
                                page.evaluate("""() => {
                                const a = document.querySelector('audio');
                                return {paused:a.paused, time:a.currentTime, ready:a.readyState,
                                    error:a.error?.message, src:a.currentSrc,
                                    status:document.querySelector('#player-status').textContent};
                            }""")
                            )
                            raise
                        catalog.get_by_role("button", name="Modifier", exact=True).click()
                        editor = page.locator(".admin-edit-dialog")
                        editor.get_by_role(
                            "button", name="Retirer la correspondance", exact=True
                        ).click()
                        expect(
                            editor.locator(".catalog-editor > div > .catalog-genres")
                        ).to_contain_text("Aucun genre renseigné")
                        assert not errors, errors
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                        context.close()
                        print(f"{width}px: audio and per-candidate completion: OK")
                finally:
                    browser.close()
        except Exception:
            log.seek(0)
            print(log.read()[-5000:])
            raise
        finally:
            server.terminate()
            server.wait(timeout=10)
