"""Isolated video lifecycle and Stats summary UI checks, with mocked YouTube playback."""

import io
import os
import socket
import subprocess
import sys
import tempfile
import time
import wave
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

with tempfile.TemporaryDirectory(prefix="radio-youtube-stats-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/test.db"
    os.environ["ADMIN_TOKEN"] = "browser-test-only"
    from app.database import SessionLocal, engine
    from app.models import Base, Play, Station
    from app.services.catalog import ensure_entry

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        for i in range(3):
            station = Station(
                name=f"Radio {i}", slug=f"radio-{i}", stream_url="https://example.test/live"
            )
            db.add(station)
            db.flush()
            entry = ensure_entry(db, "Artist", f"Song {i}")
            entry.selected = {
                "provider": "itunes",
                "provider_id": str(i),
                "artist": "Artist",
                "title": f"Song {i}",
            }
            for _ in range(i + 1):
                db.add(
                    Play(
                        station_id=station.id,
                        artist="Artist",
                        title=f"Song {i}",
                        source="icy",
                        raw_metadata="original",
                        detected_at=datetime.now(UTC),
                        last_seen_at=datetime.now(UTC),
                    )
                )
        db.add(Station(name="Empty Radio", slug="empty", stream_url="https://example.test/live"))
        db.commit()
    audio_bytes = io.BytesIO()
    with wave.open(audio_bytes, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 8000 * 4)
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
            browser = p.chromium.launch(executable_path=os.environ.get("CHROME_EXECUTABLE"))
            for width in (1280, 414, 412, 320):
                context = browser.new_context(
                    viewport={"width": width, "height": 883 if width == 412 else 846}
                )
                context.route("**/api/stations/*/artwork", lambda r: r.fulfill(status=404))
                context.route(
                    "**/fixture.wav",
                    lambda r: r.fulfill(content_type="audio/wav", body=audio_bytes.getvalue()),
                )
                context.route(
                    "https://www.youtube-nocookie.com/embed/**",
                    lambda r: r.fulfill(content_type="text/html", body="<p>Mock YouTube frame</p>"),
                )
                context.route(
                    "https://www.youtube.com/iframe_api",
                    lambda r: r.fulfill(
                        content_type="application/javascript",
                        body="""
                    window.YT={Player:function(frame,options){
                      this.playVideo=()=>{window.__ytPlayed=(window.__ytPlayed||0)+1;options.events.onStateChange({data:1});};
                      this.destroy=()=>{window.__ytDestroyed=(window.__ytDestroyed||0)+1;frame.remove();};
                      window.__ytEvents=options.events;
                      setTimeout(()=>options.events.onReady({target:this}),0);
                    }};window.onYouTubeIframeAPIReady();
                """,
                    ),
                )
                mode = ["matched"]
                calls = []

                def videos(route, _request, mode=mode, calls=calls):
                    calls.append(route.request.url)
                    if mode[0] == "error":
                        return route.fulfill(
                            status=503, json={"detail": "Vidéo temporairement indisponible."}
                        )
                    candidates = (
                        []
                        if mode[0] == "not_found"
                        else [
                            {
                                "id": "abcdefghijk",
                                "title": "Artist Song 2 official",
                                "channel": "Artist",
                            }
                        ]
                    )
                    if mode[0] == "ambiguous":
                        candidates.append(
                            {
                                "id": "ABCDEFGHIJK",
                                "title": "Artist Song 2 audio",
                                "channel": "Artist",
                            }
                        )
                    route.fulfill(json={"status": mode[0], "candidates": candidates})

                context.route("**/api/catalog/*/youtube**", videos)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                page.goto(base + "/")
                page.locator(".podium-detail").first.click()
                expect(page.locator(".catalog-platforms")).to_be_visible()
                page.locator(".catalog-platforms summary").click()
                assert not calls
                assert page.locator('script[src*="youtube.com"]').count() == 0
                page.evaluate(
                    "() => window.RadioPlayer.preview("
                    "{artist:'Artist',title:'Song 2'}, '/fixture.wav')"
                )
                page.wait_for_function('() => !document.querySelector("audio").paused')
                page.get_by_role("button", name="YouTube", exact=True).click()
                expect(page.locator(".youtube-viewport iframe")).to_have_count(1)
                page.wait_for_function("() => window.__ytPlayed > 0")
                assert page.locator("audio").evaluate("(a)=>a.paused")
                assert len(calls) == 1
                page.evaluate("()=>window.__ytEvents.onAutoplayBlocked()")
                expect(page.locator('.catalog-youtube [role="status"]')).to_contain_text(
                    "Appuyez sur"
                )
                page.evaluate("()=>window.__ytEvents.onError()")
                expect(page.locator('.catalog-youtube [role="status"]')).to_contain_text(
                    "ne peut pas être lue"
                )
                page.get_by_role("button", name="Fermer la vidéo", exact=True).click()
                expect(page.locator(".youtube-viewport iframe")).to_have_count(0)
                mode[0] = "ambiguous"
                page.get_by_role("button", name="YouTube", exact=True).click()
                expect(page.locator(".youtube-choices button")).to_have_count(2)
                assert page.locator(".youtube-viewport iframe").count() == 0
                page.locator(".youtube-choices button").first.click()
                expect(page.locator(".youtube-viewport iframe")).to_have_count(1)
                page.locator("audio").evaluate("(a)=>a.play()")
                expect(page.locator(".youtube-viewport iframe")).to_have_count(0)
                mode[0] = "not_found"
                page.get_by_role("button", name="YouTube", exact=True).click()
                expect(page.locator('.catalog-youtube [role="status"]')).to_contain_text(
                    "Aucune vidéo"
                )
                mode[0] = "error"
                page.get_by_role("button", name="YouTube", exact=True).click()
                expect(page.locator('.catalog-youtube [role="status"]')).to_contain_text(
                    "temporairement indisponible"
                )
                mode[0] = "matched"
                page.get_by_role("button", name="YouTube", exact=True).click()
                expect(page.locator(".youtube-viewport iframe")).to_have_count(1)
                page.locator(".catalog-close").click()
                expect(page.locator(".youtube-viewport iframe")).to_have_count(0)
                # Natural excerpt endings: opt-in radio return, persistence and manual pause.
                context.route(
                    "https://example.test/live",
                    lambda r: r.fulfill(content_type="audio/wav", body=audio_bytes.getvalue()),
                )
                page.locator("#listening-settings-toggle").click()
                expect(page.locator("#resume-radio-after-preview")).not_to_be_checked()
                page.locator("#resume-radio-after-preview").check()
                expect(page.locator("#resume-radio-target")).to_contain_text("Choisissez")
                page.locator("#listening-settings-close").click()
                page.evaluate(
                    "()=>window.RadioPlayer.preview("
                    "{artist:'Artist',title:'No radio'},'/fixture.wav')"
                )
                page.wait_for_function('() => !document.querySelector("audio").paused')
                page.locator("audio").evaluate("a=>a.currentTime=a.duration-.05")
                expect(page.locator("#playback-label")).to_have_text("Extrait terminé")
                page.locator('.station-play[data-id="1"]').click()
                page.wait_for_function('() => !document.querySelector("audio").paused')
                page.evaluate(
                    "()=>window.RadioPlayer.preview("
                    "{artist:'Artist',title:'Return test'},'/fixture.wav')"
                )
                page.wait_for_function('() => !document.querySelector("audio").paused')
                page.locator("audio").evaluate("a=>a.currentTime=a.duration-.05")
                expect(page.locator(".player")).to_have_attribute("data-mode", "radio")
                page.wait_for_function('() => !document.querySelector("audio").paused')
                page.locator("#listening-settings-toggle").click()
                expect(page.locator("#resume-radio-target")).to_contain_text("Radio 0")
                page.locator("#resume-radio-after-preview").uncheck()
                page.keyboard.press("Escape")
                page.evaluate(
                    "()=>window.RadioPlayer.preview("
                    "{artist:'Artist',title:'Stop test'},'/fixture.wav')"
                )
                page.wait_for_function('() => !document.querySelector("audio").paused')
                page.locator("#player-toggle").click()
                expect(page.locator(".player")).to_have_attribute("data-mode", "preview")
                expect(page.locator("#playback-label")).to_have_text("En pause")
                page.locator("#player-toggle").click()
                page.locator("audio").evaluate("a=>a.currentTime=a.duration-.05")
                expect(page.locator("#playback-label")).to_have_text("Extrait terminé")
                page.locator("#listening-settings-toggle").click()
                page.locator("#resume-radio-after-preview").check()
                page.keyboard.press("Escape")
                page.reload()
                page.locator("#listening-settings-toggle").click()
                expect(page.locator("#resume-radio-after-preview")).to_be_checked()
                assert page.locator("#listening-settings").evaluate(
                    "e=>e.scrollWidth<=e.clientWidth"
                )
                page.keyboard.press("Escape")
                if width <= 760:
                    page.locator("#mobile-nav-toggle").click()
                page.locator('.sidebar a[href="/stats"]').click()
                expect(page.locator("#stats-summary-cards article")).to_have_count(4)
                expect(
                    page.locator("#stats-summary-cards article").nth(0).locator("strong")
                ).to_have_text("6")
                expect(
                    page.locator("#stats-summary-cards article").nth(1).locator("strong")
                ).to_have_text("3")
                expect(page.locator("#stats-summary-top")).to_contain_text("Song 2")
                assert page.locator(".stats-detail[open]").count() == 0
                page.locator('[data-metric="genres"] > summary').click()
                expect(page.locator('[data-metric="genres"] meter')).to_have_count(1)
                page.locator('[data-metric="genres"] > summary').click()
                page.locator("#repetition-hours").select_option("6")
                expect(page.locator("#stats-summary-top h3")).to_have_text(
                    "Le plus diffusé · 6 heures"
                )
                page.evaluate(
                    "()=>window.MusicFilters.hide({artist:'Artist',title:'Song 2'},false)"
                )
                expect(
                    page.locator("#stats-summary-cards article").nth(0).locator("strong")
                ).to_have_text("3")
                expect(
                    page.locator("#stats-summary-cards article").nth(1).locator("strong")
                ).to_have_text("2")
                expect(page.locator("#stats-summary-top")).to_contain_text("Song 1")
                page.locator('#stats-root form [name="station_id"]').select_option("1")
                expect(
                    page.locator("#stats-summary-cards article").nth(0).locator("strong")
                ).to_have_text("1")
                for kind in ("repetitions", "diversity", "overlap", "offsets", "genres"):
                    page.locator(f'[data-metric="{kind}"] > summary').click()
                    expect(page.locator(f'[data-metric="{kind}"] .metric-status')).not_to_have_text(
                        "Chargement…"
                    )
                page.route("**/api/stats/summary**", lambda route: route.fulfill(status=503))
                page.get_by_role("button", name="Actualiser", exact=True).click()
                expect(page.locator("#stats-summary-status")).to_contain_text(
                    "Synthèse indisponible"
                )
                expect(page.locator("#stats-summary-cards article")).to_have_count(0)
                page.unroute("**/api/stats/summary**")
                page.get_by_role("button", name="Actualiser", exact=True).click()
                expect(page.locator("#stats-summary-cards article")).to_have_count(4)
                page.locator('#stats-root form [name="station_id"]').select_option("4")
                expect(page.locator("#stats-summary-status")).to_contain_text("Aucune diffusion")
                expect(page.locator("#stats-summary-top")).to_contain_text("Aucun titre")
                expect(
                    page.locator("#stats-summary-cards article").nth(3).locator("strong")
                ).to_have_text("—")
                page.locator('#stats-root form [name="station_id"]').select_option("1")
                expect(
                    page.locator("#stats-summary-cards article").nth(0).locator("strong")
                ).to_have_text("1")
                for kind in ("repetitions", "diversity", "overlap", "offsets", "genres"):
                    page.locator(f'[data-metric="{kind}"] > summary').click()
                Path("/tmp/radio-youtube-stats").mkdir(exist_ok=True)
                for theme in ("light", "dark"):
                    page.evaluate("(t)=>document.documentElement.dataset.theme=t", theme)
                    page.screenshot(
                        path=f"/tmp/radio-youtube-stats/stats-{width}-{theme}.png", full_page=True
                    )
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                assert not errors, errors
                context.close()
            browser.close()
        print(
            "YouTube lifecycle and Stats synthesis: desktop/mobile, filters and periods verified."
        )
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
