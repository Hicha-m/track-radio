import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

with tempfile.TemporaryDirectory(prefix="radio-directory-full-") as folder:
    os.environ["DATABASE_URL"] = f"sqlite:///{folder}/test.db"
    from app.database import SessionLocal, engine
    from app.models import Base
    from app.services.station_config import apply_stations, load_stations

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        apply_stations(db, load_stations("config/stations.json"))
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
            for width in [1280, 414, 320]:
                context = browser.new_context(viewport={"width": width, "height": 900})
                context.route("**/api/stations/*/artwork", lambda r: r.fulfill(status=404))
                errors = []
                page = context.new_page()
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                page.goto(f"http://127.0.0.1:{port}/radios")
                expect(page.locator(".station-card:visible")).to_have_count(26)
                search = page.locator("#station-search")
                search.fill("SKYROCK")
                expect(page.locator(".station-card:visible")).to_have_count(18)
                search.fill("francais")
                expect(page.locator(".station-card:visible")).to_have_count(1)
                search.fill("")
                expect(page.locator(".station-card:visible")).to_have_count(26)
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                assert page.locator("#radio-player").get_attribute("src") is None
                target = Path(".plan/screenshots")
                target.mkdir(exist_ok=True)
                page.screenshot(path=str(target / f"radios-26-{width}.png"))
                page.evaluate("window.RadioNavigation.go('/')")
                expect(page.locator("#chart-list")).to_be_visible()
                expect(page.locator(".station-card:visible")).to_have_count(9)
                assert not errors, errors
                context.close()
                print(
                    width,
                    "26 radios / 18 Skyrock / accents / accueil 9 / aucune lecture : OK",
                    flush=True,
                )
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
