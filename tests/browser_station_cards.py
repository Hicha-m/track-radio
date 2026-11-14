"""Station artwork and metadata updates must never start an audio stream."""

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400">'
    '<rect width="400" height="400" fill="#714b86"/>'
    '<circle cx="200" cy="200" r="120" fill="#c99dba"/>'
    '<circle cx="200" cy="200" r="40" fill="#714b86"/></svg>'
)
LOGO = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">'
    '<rect width="40" height="40" rx="8" fill="#fa243c"/>'
    '<text x="5" y="26" fill="white" font-size="18">FM</text></svg>'
)
AUTH = {"Authorization": "Bearer browser-test-only"}

with tempfile.TemporaryDirectory(prefix="radio-station-cards-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/test.db"
    os.environ["ADMIN_TOKEN"] = "browser-test-only"
    from app.database import SessionLocal, engine
    from app.models import Base, GlobalExclusion, Play, Station
    from app.services.catalog import ensure_entry
    from app.services.exclusions import exclusion_id, normalized_pair

    Base.metadata.create_all(engine)
    original_state = None
    with SessionLocal() as db:
        now = datetime.now(UTC)
        for index, title in enumerate(
            (
                "Current",
                "No cover",
                "Broken cover",
                "Old song",
                None,
                "Previous visible",
                "Personal secret",
                "Stopped",
            )
        ):
            radio = Station(
                name=f"Radio {index}",
                slug=f"radio-{index}",
                stream_url=f"https://example.test/radio{index}.wav",
                enabled=index != 7,
            )
            db.add(radio)
            db.flush()
            if title is None:
                continue
            at = now - timedelta(hours=2) if index == 3 else now - timedelta(seconds=40)
            play = Play(
                station_id=radio.id,
                artist="Artist",
                title=title,
                raw_metadata="synthetic",
                detected_at=at,
                last_seen_at=at,
                source="json",
            )
            db.add(play)
            db.flush()
            radio.collection_state = {
                "artist": "Artist",
                "title": title,
                "play_id": play.id,
                "start": int(at.timestamp() * 1000),
            }
            if index == 0:
                original_state = dict(radio.collection_state)
            entry = ensure_entry(db, "Artist", title)
            entry.selected = {
                "provider": "itunes",
                "provider_id": str(index),
                "artist": "Artist",
                "title": title,
                "duration": 240,
                "cover_url": None
                if index == 1
                else "https://example.test/missing.svg"
                if index == 2
                else "https://example.test/cover.svg",
            }
            if index == 5:
                radio.collection_state = {
                    "artist": "Station",
                    "title": "Hidden announcement",
                    "play_id": None,
                    "start": None,
                }
        artist, title = normalized_pair("Station", "Hidden announcement")
        db.add(
            GlobalExclusion(
                id=exclusion_id(artist, title),
                artist=artist,
                title=title,
                artist_normalized=artist,
                title_normalized=title,
            )
        )
        db.commit()
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 8000 * 60)
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
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
                with SessionLocal() as db:
                    db.get(Station, 1).collection_state = original_state
                    db.commit()
                context = browser.new_context(viewport={"width": width, "height": 900})
                context.add_init_script(
                    "localStorage.setItem('radio-hidden-tracks-v1',"
                    + json.dumps(json.dumps([{"artist": "Artist", "title": "Personal secret"}]))
                    + ")"
                )
                context.route(
                    "**/api/stations/*/artwork",
                    lambda r: r.fulfill(content_type="image/svg+xml", body=LOGO),
                )
                media_requests = []

                def asset(route, _request, media_requests=media_requests):
                    if route.request.url.endswith("missing.svg"):
                        return route.fulfill(status=404)
                    if route.request.url.endswith(".wav"):
                        media_requests.append(route.request.url)
                        return route.fulfill(content_type="audio/wav", body=output.getvalue())
                    route.fulfill(content_type="image/svg+xml", body=SVG)

                context.route("https://example.test/**", asset)
                page = context.new_page()
                page.clock.install()
                errors = []
                calls = []
                page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                page.on(
                    "request",
                    lambda request, calls=calls: (
                        calls.append(request.url)
                        if "/api/stations/now-playing" in request.url
                        and "station_id=" not in request.url
                        else None
                    ),
                )
                page.goto(base + "/radios")
                expect(page.locator("#station-search")).to_be_visible()
                expect(page.locator(".station-card:visible")).to_have_count(8)
                page.locator("#station-search").fill("radio 2")
                expect(page.locator(".station-card:visible")).to_have_count(1)
                page.locator("#station-search").fill("absent")
                expect(page.locator(".station-card:visible")).to_have_count(0)
                expect(page.locator("#station-search-status")).to_contain_text("0")
                page.locator("#station-search").fill("")
                expect(page.locator(".station-card:visible")).to_have_count(8)
                Path("/tmp/radio-station-directory").mkdir(exist_ok=True)
                page.screenshot(path=f"/tmp/radio-station-directory/{width}.png")
                assert not media_requests
                expect(page.locator('.station-card[data-station-id="1"]')).to_have_attribute(
                    "data-now-status", "current"
                )
                calls.clear()
                page.evaluate("window.RadioNavigation.go('/')")
                expect(page.locator("#chart-list")).to_be_visible()

                def card(index, page=page):
                    return page.locator(f'.station-card[data-station-id="{index + 1}"]')

                expect(card(0)).to_have_attribute("data-now-status", "current")
                expect(card(0).locator(".station-title")).to_have_text("Current")
                expect(card(1).locator(".station-cover-image")).to_have_count(0)
                card(2).scroll_into_view_if_needed()
                expect(card(2).locator(".station-cover-image")).to_have_count(0)
                expect(card(3)).to_have_attribute("data-now-status", "last_known")
                expect(card(3).locator(".station-now-status")).to_contain_text(
                    "Dernier titre connu"
                )
                expect(card(4).locator(".station-title")).to_have_text("Information indisponible")
                expect(card(5)).to_have_attribute("data-now-status", "last_known")
                expect(card(5).locator(".station-title")).to_have_text("Previous visible")
                expect(card(5).locator(".station-now-status")).to_contain_text(
                    "Dernier titre connu"
                )
                for index in (6,):
                    expect(card(index)).to_have_attribute("data-now-status", "hidden")
                    expect(card(index).locator(".station-title")).to_have_text(
                        "Information indisponible"
                    )
                    expect(card(index).locator(".station-artist")).to_be_empty()
                    expect(card(index).locator(".station-cover-image")).to_have_count(0)
                    expect(card(index).locator(".station-track")).to_be_disabled()
                assert "Morceau masqué" not in page.locator("main").inner_text()
                expect(card(7)).to_have_attribute("data-now-status", "last_known")
                assert len(calls) == 1 and not media_requests, (calls, media_requests)
                assert page.locator("#radio-player").get_attribute("src") is None
                # Artwork and song details remain independent of the audio command.
                card(0).scroll_into_view_if_needed()
                expect(card(0).locator(".station-cover-image")).to_be_visible()
                page.wait_for_function(
                    "() => document.querySelector('.station-cover-image').naturalWidth>0"
                )
                card(0).locator(".station-track").click()
                expect(page.locator(".catalog-title")).to_have_text("Current")
                page.locator(".catalog-close").click()
                assert not media_requests
                # The same card picks up a new collector observation without any stream connection.
                with SessionLocal() as db:
                    radio = db.get(Station, 1)
                    at = datetime.now(UTC)
                    new = Play(
                        station_id=1,
                        artist="New artist",
                        title="New track",
                        raw_metadata="synthetic new",
                        detected_at=at,
                        last_seen_at=at,
                        source="icy",
                    )
                    db.add(new)
                    db.flush()
                    radio.collection_state = {
                        "artist": "New artist",
                        "title": "New track",
                        "play_id": new.id,
                        "start": None,
                    }
                    entry = ensure_entry(db, "New artist", "New track")
                    entry.selected = {
                        "provider": "itunes",
                        "provider_id": "new",
                        "cover_url": "https://example.test/new.svg",
                        "duration": 240,
                    }
                    db.commit()
                # The visible-page polling interval picks up the new source observation.
                page.clock.run_for(30010)
                expect(card(0).locator(".station-title")).to_have_text("New track")
                expect(card(0).locator(".station-artist")).to_have_text("New artist")
                assert not media_requests
                # Source failure downgrades known information; it never calls it current.
                page.route("**/api/stations/now-playing*", lambda r: r.fulfill(status=503))
                page.evaluate("window.dispatchEvent(new Event('radio:catalogchange'))")
                expect(card(0)).to_have_attribute("data-now-status", "last_known")
                page.unroute("**/api/stations/now-playing*")
                page.evaluate("window.dispatchEvent(new Event('radio:catalogchange'))")
                expect(card(0)).to_have_attribute("data-now-status", "current")
                # A response already in flight cannot restore a song hidden in this browser.
                page.evaluate("""async () => {
                    window.__fetch=window.MusicFilters.fetch;
                    const params=window.MusicFilters.apply(new URLSearchParams());
                    const response=await window.__fetch('/api/stations/now-playing?'+params);
                    window.__snapshot=await response.json();
                    window.__pending=[];
                    window.__filterRequest=(url,...args)=>url.includes('/api/stations/now-playing')
                        ? new Promise(resolve=>window.__pending.push(data=>{
                            resolve(new Response(JSON.stringify(data)));
                          }))
                        : window.__fetch(url,...args);
                    window.MusicFilters.fetch=window.__filterRequest;
                    window.MusicFilters.live=(url,...args)=>window.__filterRequest(url,...args);
                    window.dispatchEvent(new Event('radio:catalogchange'));
                }""")
                page.wait_for_function("() => window.__pending.length===1")
                page.evaluate(
                    "window.MusicFilters.hide({artist:'New artist',title:'New track'},false)"
                )
                page.wait_for_function("() => window.__pending.length===2")
                expect(card(0).locator(".station-cover-image")).to_have_count(0)
                page.evaluate("""() => {
                    const safe=structuredClone(window.__snapshot);
                    safe.items[0]={station_id:1,status:'hidden',track:null,observed_at:null,fresh_until:null};
                    window.__pending[1](safe);
                }""")
                expect(card(0)).to_have_attribute("data-now-status", "hidden")
                page.evaluate(
                    "window.__pending[0](window.__snapshot);"
                    "window.MusicFilters.fetch=window.__fetch;"
                    "window.MusicFilters.live=(url,options)=>window.__fetch(url,options,false)"
                )
                expect(card(0).locator(".station-title")).to_have_text("Information indisponible")
                assert not media_requests
                # Global masking also applies to the actual latest song in admin mode.
                response = context.request.post(
                    base + "/api/admin/exclusions",
                    data={"artist": "Artist", "title": "No cover"},
                    headers=AUTH,
                )
                assert response.status == 200
                exclusion = response.json()["id"]
                page.evaluate("window.dispatchEvent(new Event('radio:catalogchange'))")
                expect(card(1)).to_have_attribute("data-now-status", "hidden")
                page.evaluate("window.RadioNavigation.go('/admin')")
                expect(page.locator("#admin-login")).to_be_visible()
                page.locator("#admin-login input").fill("browser-test-only")
                page.locator("#admin-login button").click()
                expect(page).to_have_url(base + "/admin/dashboard")
                page.evaluate("window.RadioNavigation.go('/')")
                expect(card(0)).to_have_attribute("data-now-status", "last_known")
                expect(card(1)).to_have_attribute("data-now-status", "hidden")
                expect(card(6)).to_have_attribute("data-now-status", "hidden")

                # Freshness expires locally without waiting for another metadata request.
                page.route("**/api/stations/now-playing*", lambda r: r.fulfill(status=503))
                page.evaluate("window.dispatchEvent(new Event('radio:catalogchange'))")
                expect(card(2)).to_have_attribute("data-now-status", "last_known")
                page.unroute("**/api/stations/now-playing*")

                def short_lived(route):
                    data = route.fetch().json()
                    item = next(row for row in data["items"] if row["station_id"] == 3)
                    item["status"] = "current"
                    expires = datetime.fromisoformat(data["checked_at"]) + timedelta(seconds=2)
                    item["fresh_until"] = expires.isoformat()
                    route.fulfill(json=data)

                page.route("**/api/stations/now-playing*", short_lived)
                page.evaluate("window.dispatchEvent(new Event('radio:catalogchange'))")
                expect(card(2)).to_have_attribute("data-now-status", "current")
                page.clock.run_for(2500)
                expect(card(2)).to_have_attribute("data-now-status", "last_known")
                page.unroute("**/api/stations/now-playing*")
                # Hidden pages and pages without cards do not poll; one batch resumes on return.
                page.evaluate(
                    "Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});"
                    "document.dispatchEvent(new Event('visibilitychange'))"
                )
                previous = len(calls)
                page.clock.run_for(30010)
                assert len(calls) == previous
                page.evaluate(
                    "Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});"
                    "document.dispatchEvent(new Event('visibilitychange'))"
                )
                expect(card(2)).to_have_attribute("data-now-status", "current")
                assert len(calls) == previous + 1
                # Clicking a station name opens its filtered broadcast history.
                card(0).locator(".station-history-open").click()
                expect(page.locator(".station-history-dialog")).to_be_visible()
                expect(page.locator(".station-history-items")).to_contain_text("Current")
                page.locator(".station-history-heading .text-button").click()
                card(6).locator(".station-history-open").click()
                expect(page.locator(".station-history-dialog")).to_be_visible()
                expect(page.locator(".station-history-items li")).to_have_count(0)
                expect(page.locator(".station-history-dialog [role=status]")).to_have_text(
                    "Aucune diffusion visible pour cette radio."
                )
                page.locator(".station-history-heading .text-button").click()
                # Explicit listening opens audio; metadata refreshes preserve that connection.
                card(0).locator(".station-play").click()
                page.wait_for_function(
                    "() => {const a=document.querySelector('#radio-player');"
                    "return !a.paused&&a.readyState>=3;}"
                )
                assert len(media_requests) == 1
                expect(page.locator("#track-title")).to_have_text("Dernier titre connu : Current")
                page.locator("#player-mode-toggle").click()
                expect(page.locator("#now-playing-mode")).to_be_visible()
                expect(page.locator("#now-playing-title")).to_contain_text("Current")
                expect(page.locator("#now-playing-source")).to_contain_text("Radio en direct")
                if width in (1280, 320):
                    page.screenshot(path=f"/tmp/radio-station-cards/{width}-now-playing.png")
                page.locator("#now-playing-close").click()
                source = page.locator("#radio-player").get_attribute("src")
                page.evaluate("window.dispatchEvent(new Event('radio:catalogchange'))")
                expect(card(0)).to_have_attribute("data-now-status", "last_known")
                assert page.locator("#radio-player").get_attribute("src") == source
                assert len(media_requests) == 1
                # The experimental mode adds a separate playlist entry without replacing live play.
                page.locator("#listening-settings-toggle").click()
                page.locator("#experimental-radio-youtube").check()
                expect(card(0).locator(".station-youtube-play")).to_be_visible()
                page.locator("#listening-settings-close").click()
                page.route(
                    "**/api/playlists/daily*",
                    lambda route: route.fulfill(
                        json={
                            "date": "2026-11-12",
                            "items": [
                                {
                                    "track_id": "a" * 64,
                                    "artist": "Artist",
                                    "title": "Current",
                                    "plays": 1,
                                    "video": {
                                        "id": "abcdefghijk",
                                        "title": "Current official",
                                        "channel": "Artist",
                                    },
                                    "candidates": [],
                                    "state": "matched",
                                }
                            ],
                        }
                    ),
                )
                card(0).locator(".station-youtube-play").click()
                expect(page.locator(".playlist-dialog")).to_be_visible()
                expect(page.locator(".playlist-items")).to_contain_text("Current")
                expect(page.locator("[data-playlist-play]")).to_be_enabled()
                page.locator("[data-playlist-close]").click()
                Path("/tmp/radio-station-cards").mkdir(exist_ok=True)
                for theme in ("light", "dark"):
                    page.evaluate("(theme)=>document.documentElement.dataset.theme=theme", theme)
                    page.locator(".stations").scroll_into_view_if_needed()
                    page.screenshot(
                        path=f"/tmp/radio-station-cards/{width}-{theme}.png", full_page=True
                    )
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                page.evaluate("window.RadioNavigation.go('/stats')")
                expect(page.locator("#stats-root")).to_be_visible()
                before = len(calls)
                page.clock.run_for(30010)
                assert len(calls) == before
                assert not errors, errors
                assert (
                    context.request.delete(
                        base + f"/api/admin/exclusions/{exclusion}", headers=AUTH
                    ).status
                    == 200
                )
                context.close()
                print(
                    f"{width}px: live/stale/hidden cards, covers, updates, "
                    "race, admin and audio isolation OK",
                    flush=True,
                )
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
