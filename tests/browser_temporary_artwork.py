"""Temporary cover attribution and replacement at desktop/mobile widths."""

import os
import socket
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import expect, sync_playwright

with tempfile.TemporaryDirectory(prefix="radio-artwork-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/test.db"
    from app.database import SessionLocal, engine
    from app.models import Base, Station, TrackMetadata
    from app.services.catalog import ensure_entry

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.add(Station(name="Radio", slug="radio", stream_url="https://example.test/audio"))
        entry = ensure_entry(db, "Phil Collins", "You Can't Hurry Love")
        entry.temporary_cover_url = "https://example.test/temporary.svg"
        entry.attempts = {
            "artwork": {
                "status": "matched",
                "score": 100,
                "threshold": 100,
                "provider": "itunes",
                "url": "https://music.apple.com/track/42",
                "cover_url": entry.temporary_cover_url,
            }
        }
        db.commit()
        key = entry.id
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
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
            browser = p.chromium.launch(executable_path="/usr/bin/google-chrome")
            for width in (1280, 414, 320):
                with SessionLocal() as db:
                    entry = db.get(TrackMetadata, key)
                    entry.selected = None
                    db.commit()
                page = browser.new_page(viewport={"width": width, "height": 900})
                errors = []
                page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                page.route(
                    "https://example.test/*.svg",
                    lambda r: r.fulfill(
                        content_type="image/svg+xml",
                        body=(
                            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
                            '<rect width="100" height="100" fill="blue"/></svg>'
                        ),
                    ),
                )
                page.goto(f"http://127.0.0.1:{port}")
                page.evaluate("window.MusicFilters.refresh()")
                page.evaluate(
                    "track => window.TrackCatalog.open(track)",
                    {"artist": "Phil Collins", "title": "You Can't Hurry Love"},
                )
                credit = page.locator(".catalog-summary a.hint")
                expect(credit).to_contain_text("score de correspondance 100/100")
                expect(credit).to_have_attribute("href", "https://music.apple.com/track/42")
                expect(page.locator(".catalog-art")).to_have_attribute(
                    "src", "https://example.test/temporary.svg"
                )
                assert page.locator(".catalog-dialog").evaluate(
                    "e => e.scrollWidth <= e.clientWidth"
                )
                page.locator(".catalog-close").click()
                with SessionLocal() as db:
                    entry = db.get(TrackMetadata, key)
                    entry.selected = {
                        "provider": "itunes",
                        "provider_id": "42",
                        "cover_url": "https://example.test/validated.svg",
                    }
                    db.commit()
                page.reload()
                page.evaluate("window.MusicFilters.refresh()")
                page.evaluate(
                    "track => window.TrackCatalog.open(track)",
                    {"artist": "Phil Collins", "title": "You Can't Hurry Love"},
                )
                expect(page.locator(".catalog-art")).to_have_attribute(
                    "src", "https://example.test/validated.svg"
                )
                expect(page.locator(".catalog-summary a.hint")).to_have_count(0)
                assert not errors, errors
                print(width, "pochette / score / attribution / remplacement : OK", flush=True)
                page.close()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
