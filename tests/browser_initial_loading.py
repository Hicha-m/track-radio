"""Public reads and listening remain usable with stalled session/revision checks."""

import io
import os
import socket
import subprocess
import sys
import tempfile
import time
import wave
from datetime import UTC, datetime, timedelta

from playwright.sync_api import expect, sync_playwright

with tempfile.TemporaryDirectory(prefix="radio-startup-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/test.db"
    from app.database import SessionLocal, engine
    from app.models import Base, Play, Station
    from tests.browser_prepared import start

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        radio = Station(name="Test", slug="test", stream_url="https://example.test/live")
        db.add(radio)
        db.flush()
        now = datetime.now(UTC) - timedelta(seconds=5)
        db.add(
            Play(
                station_id=radio.id,
                artist="Artist",
                title="Song",
                detected_at=now,
                last_seen_at=now,
                source="json",
                raw_metadata="fixture",
            )
        )
        db.commit()
    close = start(SessionLocal)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 8000 * 20)
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
                page = browser.new_page(viewport={"width": width, "height": 900})
                held = []
                page.route("**/api/admin/session", lambda r, request, held=held: held.append(r))
                page.route(
                    "**/api/exclusions/revision", lambda r, request, held=held: held.append(r)
                )
                page.route(
                    "https://example.test/live",
                    lambda r: r.fulfill(content_type="audio/wav", body=output.getvalue()),
                )
                page.goto(f"http://127.0.0.1:{port}", wait_until="domcontentloaded")
                expect(page.locator(".station-title")).to_have_text("Song", timeout=2000)
                expect(page.locator(".podium-detail")).to_contain_text("Song", timeout=2000)
                assert page.evaluate("window.MusicFilters.ready()") is False
                page.locator(".station-play").click()
                page.wait_for_function(
                    "() => !document.querySelector('#radio-player').paused", timeout=2000
                )
                assert len(held) == 2
                print(
                    width, "fresh titles / ranking / listening with stalled guards: OK", flush=True
                )
                for route in held:
                    route.abort()
                page.close()
            browser.close()
    finally:
        close()
        server.terminate()
        server.wait(timeout=10)
