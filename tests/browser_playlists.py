"""Composer journeys on synthetic history; YouTube API events are controlled locally."""

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

with tempfile.TemporaryDirectory(prefix="radio-playlist-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/test.db"
    os.environ["ADMIN_TOKEN"] = "browser-test-only"
    from app.database import SessionLocal, engine
    from app.models import Base, GlobalExclusion, Play, Station, YouTubeMatch
    from app.services.catalog import ensure_entry
    from app.services.exclusions import exclusion_id, normalized_pair

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        station = Station(name="Test FM", slug="test", stream_url="https://example.test/audio")
        db.add(station)
        db.flush()
        keys = {}
        played_at = datetime.now(UTC) - timedelta(minutes=1)
        for index, title in enumerate(
            ["First", "Second", "Ambiguous", "Missing", "Global secret", "Personal secret"]
        ):
            track = ensure_entry(db, "Artist", title)
            keys[title] = track.id
            for _ in range(7 - index):
                db.add(
                    Play(
                        station_id=station.id,
                        artist="Artist",
                        title=title,
                        detected_at=played_at,
                        last_seen_at=played_at,
                        source="json",
                        raw_metadata="synthetic",
                    )
                )
            videos = [{"id": chr(97 + index) * 11, "title": "Artist " + title, "channel": "Artist"}]
            status = "matched"
            if title == "Ambiguous":
                status = "ambiguous"
                videos.append({"id": "zzzzzzzzzzz", "title": "Another choice", "channel": "Artist"})
            if title == "Missing":
                status, videos = "not_found", []
            db.add(
                YouTubeMatch(
                    track_id=track.id,
                    artist="Artist",
                    title=title,
                    status=status,
                    candidates=videos,
                    selected=videos[0] if status == "matched" else None,
                    requested_at=datetime.now(UTC),
                )
            )
        a, t = normalized_pair("Artist", "Global secret")
        db.add(
            GlobalExclusion(
                id=exclusion_id(a, t), artist=a, title=t, artist_normalized=a, title_normalized=t
            )
        )
        db.commit()
    from tests.browser_prepared import start as start_prepared_rankings

    stop_prepared_rankings = start_prepared_rankings(SessionLocal)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
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
                context = browser.new_context(
                    viewport={"width": width, "height": 900}, accept_downloads=True
                )
                context.add_init_script(
                    "localStorage.setItem('radio-hidden-tracks-v1',"
                    + json.dumps(json.dumps([{"artist": "Artist", "title": "Personal secret"}]))
                    + ")"
                )
                context.route("https://example.test/**", lambda route: route.fulfill(status=404))
                context.route(
                    "https://www.youtube-nocookie.com/embed/**",
                    lambda route: route.fulfill(
                        content_type="text/html", body="<p>Video fixture</p>"
                    ),
                )
                context.route(
                    "https://www.youtube.com/iframe_api",
                    lambda route: route.fulfill(
                        content_type="application/javascript",
                        body="""
window.YT={Player:function(frame,options){
 let state=0;this.playVideo=()=>{state=1;options.events.onStateChange({data:1});};
 this.pauseVideo=()=>{state=2;options.events.onStateChange({data:2});};
 this.getPlayerState=()=>state;this.getDuration=()=>240;this.getCurrentTime=()=>0;
 this.seekTo=()=>{};this.getVolume=()=>100;this.setVolume=()=>{};
 this.mute=()=>{};this.unMute=()=>{};this.isMuted=()=>false;this.destroy=()=>frame.remove();
 window.__events=options.events;setTimeout(()=>options.events.onReady({target:this}),0);
}};window.onYouTubeIframeAPIReady();
""",
                    ),
                )
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                media = []
                page.on(
                    "request",
                    lambda req, media=media: (
                        media.append(req.url)
                        if "/embed/" in req.url or req.url.endswith("/audio")
                        else None
                    ),
                )
                page.goto(base)
                expect(page.locator("#chart-list")).to_have_attribute("aria-busy", "false")
                if not page.locator("[data-open-playlist]").is_visible():
                    page.locator("#chart-controls > summary").click()
                page.locator("[data-open-playlist]").click()
                panel = page.locator(".playlist-dialog")
                panel.locator("[data-playlist-form] button[type=submit]").click()
                expect(panel.locator("li")).to_have_count(4)
                assert "secret" not in panel.inner_text()
                assert not media
                assert panel.locator("[data-playlist-play]").is_disabled()
                panel.locator("[data-playlist-resolve]").click()
                expect(panel.locator("[data-playlist-status]")).to_contain_text(
                    "Recherche terminée"
                )
                expect(panel.locator("li").nth(3)).to_contain_text("Aucune vidéo trouvée")
                ambiguous = panel.locator("li").nth(2)
                ambiguous.locator("select").select_option("ccccccccccc")
                expect(ambiguous).to_contain_text("Vidéo sélectionnée")
                # Reorder and deselect while keeping unresolved songs exportable.
                panel.get_by_role("button", name="Monter Second", exact=True).click()
                expect(panel.locator("li").first).to_contain_text("Second")
                panel.get_by_role("checkbox", name="Inclure Missing", exact=True).uncheck()
                for destination in ["youtube", "spotify"]:
                    panel.locator("[data-playlist-destination]").select_option(destination)
                    with page.expect_download() as pending:
                        panel.locator("[data-playlist-export]").click()
                    downloaded = pending.value
                    rows = list(
                        csv.DictReader(
                            io.StringIO(Path(downloaded.path()).read_text(encoding="utf-8-sig")),
                            delimiter=";",
                        )
                    )
                    assert [row["title"] for row in rows] == ["Second", "First", "Ambiguous"]
                    assert all(
                        ("youtube.com" if destination == "youtube" else "spotify.com") in row["url"]
                        for row in rows
                    )
                assert not media
                panel.locator("[data-playlist-play]").click()
                expect(panel.locator("[data-playlist-status]")).to_contain_text(
                    "Lecture 1/3 : Second"
                )
                expect(panel.locator("#youtube-dock iframe")).to_have_attribute(
                    "src", __import__("re").compile("/bbbbbbbbbbb")
                )
                # CSS visibility changes preserve the iframe and active playback.
                panel.locator("[data-playlist-video-visible]").uncheck()
                expect(page.locator("#youtube-dock iframe")).not_to_be_visible()
                expect(page.locator("#youtube-dock iframe")).to_have_count(1)
                expect(page.locator("#player-toggle")).to_have_attribute("aria-pressed", "true")
                panel.locator("[data-playlist-video-visible]").check()
                expect(page.locator("#youtube-dock iframe")).to_be_visible()
                panel.locator("[data-playlist-pause]").click()
                panel.locator("[data-playlist-pause]").click()
                page.wait_for_function("window.__events !== undefined")
                page.evaluate("window.__events.onStateChange({data:0})")
                expect(panel.locator("[data-playlist-status]")).to_contain_text(
                    "Lecture 2/3 : First"
                )
                expect(page.locator("#youtube-dock iframe")).to_have_attribute(
                    "src", __import__("re").compile("/aaaaaaaaaaa")
                )
                page.wait_for_timeout(100)
                page.evaluate("window.__events.onError({data:150})")
                expect(panel.locator("[data-playlist-status]")).to_contain_text(
                    "Lecture 3/3 : Ambiguous"
                )
                page.wait_for_timeout(100)
                page.evaluate("window.__events.onStateChange({data:0})")
                expect(panel.locator("[data-playlist-status]")).to_contain_text(
                    "Fin de la sélection"
                )
                # Filter change clears all rows and destroys the active iframe immediately.
                page.evaluate("window.MusicFilters.hide({artist:'Artist',title:'First'},false)")
                expect(panel.locator("li")).to_have_count(0)
                expect(page.locator("#youtube-dock iframe")).to_have_count(0)
                panel.locator("[data-playlist-form] button[type=submit]").click()
                expect(panel.locator("li")).to_have_count(3)
                # A provider/quota failure stops at one request, with no loop.
                calls = []

                def quota(route, _request, calls=calls):
                    calls.append(route.request.url)
                    route.fulfill(status=429, json={"detail": "Quota de test atteint"})

                page.route("**/api/catalog/*/youtube?*", quota)
                panel.locator("[data-playlist-resolve]").click()
                expect(panel.locator("[data-playlist-status]")).to_contain_text(
                    "Quota de test atteint"
                )
                assert len(calls) == 1
                page.unroute("**/api/catalog/*/youtube?*", quota)
                held = []

                def delayed(route, _request, held=held):
                    held.append(route)

                page.route("**/api/catalog/*/youtube?*", delayed)
                panel.locator("[data-playlist-resolve]").click()
                expect(panel.locator("[data-playlist-status]")).to_contain_text("Recherche 1/3")
                page.wait_for_timeout(100)
                assert len(held) == 1
                panel.locator("[data-playlist-stop]").click()
                held[0].fulfill(
                    json={
                        "status": "matched",
                        "candidates": [
                            {"id": "bbbbbbbbbbb", "title": "Late result", "channel": "Artist"}
                        ],
                        "selected": {
                            "id": "bbbbbbbbbbb",
                            "title": "Late result",
                            "channel": "Artist",
                        },
                    }
                )
                expect(panel.locator("[data-playlist-status]")).to_contain_text("Recherche annulée")
                assert "Late result" not in panel.inner_text()
                page.unroute("**/api/catalog/*/youtube?*", delayed)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                evidence = Path("/tmp/radio-playlist-evidence")
                evidence.mkdir(exist_ok=True)
                for theme in ["light", "dark"]:
                    page.evaluate("theme=>document.documentElement.dataset.theme=theme", theme)
                    page.screenshot(path=str(evidence / f"{width}-{theme}.png"))
                panel.locator("[data-playlist-close]").click()
                page.locator("[data-open-playlist]").click()
                expect(panel.locator("li")).to_have_count(0)
                panel.locator("[data-playlist-close]").click()
                page.locator("#listening-settings-toggle").click()
                page.locator("#autoplay-tracks").uncheck()
                page.keyboard.press("Escape")
                page.evaluate("""() => {
                    const key = 'radio-hidden-tracks-v1';
                    const hidden = JSON.parse(localStorage.getItem(key) || '[]');
                    const remaining = hidden.filter(track => track.title !== 'First');
                    localStorage.setItem(key, JSON.stringify(remaining));
                }""")
                page.reload()
                expect(page.locator("#chart-list")).to_have_attribute("aria-busy", "false")
                if not page.locator("[data-open-daily]").is_visible():
                    page.locator("#chart-controls > summary").click()
                page.locator("[data-open-daily]").click()
                expect(page.locator(".playlist-dialog h2")).to_have_text("Daily du jour")
                expect(page.locator("[data-playlist-items] li")).to_have_count(4)
                assert [
                    text.strip()
                    for text in page.locator("[data-playlist-items] strong").all_text_contents()
                ] == [
                    "First · Artist",
                    "Second · Artist",
                    "Ambiguous · Artist",
                    "Missing · Artist",
                ]
                daily_evidence = Path("/tmp/radio-daily-evidence")
                daily_evidence.mkdir(exist_ok=True)
                page.screenshot(path=str(daily_evidence / f"{width}-daily.png"))
                expect(page.locator("[data-playlist-play]")).to_be_enabled()
                expect(page.locator("[data-playlist-previous]")).to_be_disabled()
                expect(page.locator("[data-playlist-next]")).to_be_disabled()
                page.locator("[data-playlist-play]").click()
                expect(page.locator("[data-playlist-status]")).to_have_text("Lecture 1/2 : First")
                expect(page.locator("[data-playlist-items] li").first).to_have_attribute(
                    "aria-current", "true"
                )
                expect(page.locator("[data-playlist-next]")).to_be_enabled()
                page.locator("[data-playlist-next]").click()
                expect(page.locator("[data-playlist-status]")).to_have_text("Lecture 2/2 : Second")
                expect(page.locator("#youtube-dock iframe")).to_have_attribute(
                    "src", __import__("re").compile("/bbbbbbbbbbb")
                )
                page.locator("[data-playlist-previous]").click()
                expect(page.locator("[data-playlist-status]")).to_have_text("Lecture 1/2 : First")
                expect(page.locator("#youtube-dock iframe")).to_have_attribute(
                    "src", __import__("re").compile("/aaaaaaaaaaa")
                )
                page.wait_for_timeout(100)
                page.evaluate("window.__events.onStateChange({data:0})")
                expect(page.locator("[data-playlist-status]")).to_contain_text(
                    "Fin de la sélection"
                )
                assert not errors, errors
                print(
                    f"{width}px: filtered selection, order, choices, exports, "
                    "playback sequence, error, reset OK",
                    flush=True,
                )
                context.close()
            browser.close()
    finally:
        stop_prepared_rankings()
        server.terminate()
        server.wait(timeout=10)
