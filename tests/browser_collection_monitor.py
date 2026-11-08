"""Real admin UI and SQLite, synthetic evidence only; desktop/mobile and recovery."""

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
CAPTURES = Path("/tmp/radio-collection-monitor")
CAPTURES.mkdir(exist_ok=True)

with tempfile.TemporaryDirectory(prefix="radio-monitor-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/test.db"
    os.environ["ADMIN_TOKEN"] = "browser-monitor-only"
    from app.database import SessionLocal, engine
    from app.models import Base, CollectionEvent, CollectionStatus, Station
    from app.services import collection_diagnostics as diag
    from app.services.supervision import report

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        station = Station(
            name="Skyrock",
            slug="skyrock",
            source_type="icy",
            stream_url="https://icecast.skyrock.net/s/natio_mp3_128k",
        )
        other = Station(
            name="Radio test", slug="radio-test", stream_url="https://example.test/stream"
        )
        db.add_all([station, other])
        db.commit()
        station_id = station.id
    report(SessionLocal, "collector", "completed", total=2, completed=2, failed=0)

    def simulate(outcome):
        event_id = diag.begin(SessionLocal, station_id, "icy")
        trace = diag.Trace(event_id=event_id)
        trace.responses = [
            {"status": 403 if outcome == "http_error" else 200, "cache": ["no-cache"]}
        ]
        diag.finish(SessionLocal, trace, outcome, changed=outcome == "new")
        if outcome == "unchanged":
            with SessionLocal() as db:
                db.get(CollectionStatus, station_id).last_change_at = datetime.now(UTC) - timedelta(
                    hours=1
                )
                db.commit()

    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    log = open(Path(directory) / "server.log", "w+")
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
    base = f"http://127.0.0.1:{port}"
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
                for theme in ("light", "dark"):
                    context = browser.new_context(viewport={"width": width, "height": 900})
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                    page.goto(base + "/admin/workers")
                    expect(page.locator("#admin-login")).to_be_visible()
                    page.locator("#admin-login input").fill("browser-monitor-only")
                    page.locator("#admin-login button").click()
                    expect(page.locator("#collection-monitor")).to_be_visible()
                    page.evaluate("(theme)=>document.documentElement.dataset.theme=theme", theme)
                    form = page.locator("#collection-filters")
                    form.locator("[name=station_id]").select_option(str(station_id))
                    form.locator("[name=hours]").select_option("6")
                    card = page.locator(f'.collection-station[data-station-id="{station_id}"]')
                    for outcome, label in [
                        ("http_error", "Refus ou erreur HTTP"),
                        ("timeout", "Délai dépassé"),
                        ("network_error", "Erreur réseau"),
                        ("format_error", "Format inattendu"),
                        ("empty", "Extraction vide"),
                        ("new", "Nouveau passage"),
                        ("unchanged", "Contenu inchangé"),
                    ]:
                        simulate(outcome)
                        form.locator("button").click()
                        expect(page.locator(".collection-event").first).to_have_attribute(
                            "data-outcome", outcome
                        )
                        expect(card).to_contain_text(label)
                    expect(card).to_have_attribute("data-health", "unchanged")
                    page.locator(".collection-event").first.locator("summary").click()
                    expect(page.locator(".collection-event").first.locator("dl")).to_be_visible()
                    form.locator("[name=outcome]").select_option("http_error")
                    expect(page.locator(".collection-event").first).to_have_attribute(
                        "data-outcome", "http_error"
                    )
                    form.locator("[name=outcome]").select_option("")
                    form.locator("[name=station_id]").select_option(str(other.id))
                    expect(page.locator(".collection-station")).to_have_count(1)
                    expect(page.locator(".collection-station")).to_contain_text("Radio test")
                    form.locator("[name=station_id]").select_option(str(station_id))
                    expect(card).to_be_visible()
                    with SessionLocal() as db:
                        state = db.get(CollectionStatus, station_id)
                        state.probe_requested_at = None
                        db.query(CollectionEvent).filter(CollectionEvent.kind == "probe").delete()
                        db.commit()
                    form.locator("button").click()
                    probe = card.get_by_role("button", name="Sonder ICY et JSON depuis le worker")
                    expect(probe).to_be_enabled()
                    probe.click()
                    form.locator("[name=kind]").select_option("probe")
                    expect(page.locator(".collection-event")).to_have_count(3)
                    expect(page.locator(".collection-event").first).to_have_attribute(
                        "data-outcome", "queued"
                    )
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
                    assert not errors, errors
                    page.screenshot(path=str(CAPTURES / f"{width}-{theme}.png"), full_page=True)
                    page.locator("#listening-settings-toggle").click()
                    page.locator("#language-select").select_option("en")
                    expect(page.locator("#collection-title")).to_have_text("Collection by station")
                    expect(page.locator(".collection-station").first).to_contain_text(
                        "Last attempt"
                    )
                    # Leaving the page disposes both monitor and worker refreshes.
                    page.evaluate('window.RadioNavigation.go("/admin/radios")')
                    expect(page.locator("#admin-stations")).to_be_visible()
                    context.close()
            browser.close()
        print(
            "Collection monitor: desktop/mobile, themes, filters, failures, "
            "recovery, queue and English OK"
        )
    finally:
        server.terminate()
        server.wait(timeout=10)
        log.close()
        engine.dispose()
