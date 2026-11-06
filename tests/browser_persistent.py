"""Isolated end-to-end coverage for persistent media, languages and admin supervision."""

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

CAPTURES = Path("/tmp/radio-final-captures")
CAPTURES.mkdir(exist_ok=True)
ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="radio-persistent-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/test.db"
    os.environ["ADMIN_TOKEN"] = "browser-test-only"
    from app.database import SessionLocal, engine
    from app.models import Base, Play, Station
    from app.services.catalog import ensure_entry
    from app.services.supervision import report

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        for i in range(2):
            station = Station(
                name=f"Radio {i}",
                slug=f"radio-{i}",
                stream_url=f"https://example.test/live-{i}.wav",
            )
            db.add(station)
            db.flush()
            entry = ensure_entry(db, "Artist", f"Song {i}")
            entry.status = "matched"
            entry.automatic_done = True
            entry.selected = {
                "provider": "itunes",
                "provider_id": str(i),
                "artist": "Artist",
                "title": f"Song {i}",
                "preview_url": "https://example.test/preview.wav",
            }
            db.add(
                Play(
                    station_id=station.id,
                    artist="Artist",
                    title=f"Song {i}",
                    source="icy",
                    raw_metadata="test-only",
                    detected_at=datetime.now(UTC),
                    last_seen_at=datetime.now(UTC),
                )
            )
        db.commit()
    report(SessionLocal, "collector", "completed", total=2, completed=2, failed=0)
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 8000 * 60)
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    fixture = Path(directory) / "server.py"
    fixture.write_text(
        """import uvicorn
from pydantic import SecretStr
from app.config import settings
from app.services import youtube
settings.youtube_api_key = SecretStr("browser-test-only")
async def search(*args):
    return {"status":"matched","candidates":[
        {"id":"abcdefghijk","title":"Artist Song official","channel":"Artist"},
        {"id":"ABCDEFGHIJK","title":"Artist Song audio","channel":"Artist"}]}
youtube.search_remote = search
uvicorn.run("app.main:app", host="127.0.0.1", port="""
        + str(port)
        + ")\n"
    )
    log = open(Path(directory) / "server.log", "w+")
    server = subprocess.Popen(
        [sys.executable, str(fixture)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
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
            browser = p.chromium.launch(
                executable_path="/usr/bin/google-chrome",
                args=["--autoplay-policy=no-user-gesture-required"],
            )
            for width in (1280, 414, 412, 320):
                context = browser.new_context(
                    viewport={"width": width, "height": 900 if width == 1280 else 883}
                )
                context.route(
                    "https://example.test/**",
                    lambda route: route.fulfill(content_type="audio/wav", body=audio.getvalue()),
                )
                context.route("**/api/stations/*/artwork", lambda route: route.fulfill(status=404))
                context.route(
                    "https://www.youtube-nocookie.com/embed/**",
                    lambda route: route.fulfill(
                        content_type="text/html", body="<p>Isolated video fixture</p>"
                    ),
                )
                context.route(
                    "https://www.youtube.com/iframe_api",
                    lambda route: route.fulfill(
                        content_type="application/javascript",
                        body="""
window.__ytCreates=0;window.__ytDestroys=0;
window.YT={Player:function(frame,options){
 window.__ytCreates++;let state=0,volume=100,muted=false,position=0;
 this.playVideo=()=>{state=1;options.events.onStateChange({data:1});};
 this.pauseVideo=()=>{state=2;options.events.onStateChange({data:2});};
 this.getPlayerState=()=>state;this.getDuration=()=>240;this.getCurrentTime=()=>position;
 this.seekTo=(value)=>{position=value;};this.getVolume=()=>volume;this.setVolume=(value)=>{volume=value;};
 this.mute=()=>{muted=true;};this.unMute=()=>{muted=false;};this.isMuted=()=>muted;
 this.destroy=()=>{window.__ytDestroys++;frame.remove();};
 window.__yt=this;window.__ytEvents=options.events;setTimeout(()=>options.events.onReady({target:this}),0);
}};window.onYouTubeIframeAPIReady();""",
                    ),
                )
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                page.goto(base)
                expect(page.locator("#chart-podium .podium-card")).to_have_count(2)
                assert page.locator("iframe").count() == 0
                # Live stream remains the same, playing but muted during preview/pause/video.
                page.locator(".station-play").first.click()
                page.wait_for_function('() => !document.querySelector("#radio-player").paused')
                page.evaluate(
                    'window.__radioNode=document.querySelector("#radio-player");window.__radioURL=window.__radioNode.src'
                )
                page.locator("#player-toggle").click()
                assert page.locator("#radio-player").evaluate("(a)=>!a.paused && a.muted")
                page.locator("#player-toggle").click()
                assert page.locator("#radio-player").evaluate("(a)=>!a.paused && !a.muted")
                page.evaluate(
                    'window.RadioPlayer.preview({title:"Fixture preview",artist:"Artist"},"https://example.test/preview.wav")'
                )
                page.wait_for_function('() => !document.querySelector("#excerpt-player").paused')
                assert page.locator("#radio-player").evaluate(
                    "(a)=>!a.paused && a.muted && a.src===window.__radioURL"
                )
                page.locator("#player-return-radio").click()
                assert page.locator("#radio-player").evaluate(
                    "(a)=>a===window.__radioNode && a.src===window.__radioURL && !a.muted"
                )
                # Same video iframe after closing the entry and navigating.
                page.evaluate('window.TrackCatalog.open({title:"Song 0",artist:"Artist"})')
                expect(
                    page.locator(".catalog-dialog .preview-entry .youtube-button").last
                ).to_be_visible()
                page.locator(".catalog-dialog .preview-entry .youtube-button").last.click()
                page.wait_for_function("() => window.__yt?.getPlayerState()===1")
                page.evaluate('window.__frame=document.querySelector(".youtube-viewport iframe")')
                assert page.locator("#radio-player").evaluate("(a)=>!a.paused && a.muted")
                page.locator(".catalog-dialog .catalog-close").click()
                expect(page.locator("#youtube-dock.is-floating")).to_be_visible()
                page.locator("#youtube-dock button").filter(has_text="Plein écran").click()
                page.wait_for_function("() => Boolean(document.fullscreenElement)")
                page.evaluate("document.exitFullscreen()")
                page.locator("#player-toggle").click()
                assert page.evaluate("window.__yt.getPlayerState()") == 2
                page.locator("#player-toggle").click()
                assert page.evaluate("window.__yt.getPlayerState()") == 1
                page.locator("#player-volume").fill("0.35")
                assert page.evaluate("window.__yt.getVolume()") == 35
                page.locator("#player-mute").click()
                assert page.evaluate("window.__yt.isMuted()")
                expect(page.locator("#player-volume")).to_have_value("0.35")
                page.locator("#player-mute").click()
                assert not page.evaluate("window.__yt.isMuted()")
                page.locator("#player-volume").fill("0")
                assert page.evaluate("window.__yt.getVolume()") == 0
                page.locator("#player-volume").fill("0.35")
                page.locator("#player-progress").fill("500")
                assert page.evaluate("window.__yt.getCurrentTime()") == 120
                page.evaluate('window.RadioNavigation.go("/recherche")')
                expect(page.locator("#explorer-form")).to_be_visible()
                assert page.evaluate(
                    'document.querySelector(".youtube-viewport iframe")===window.__frame'
                    " && window.__ytCreates===1 && window.__ytDestroys===0"
                )
                page.screenshot(path=str(CAPTURES / f"video-floating-{width}.png"), full_page=True)
                page.evaluate('window.TrackCatalog.open({title:"Song 0",artist:"Artist"})')
                expect(page.locator(".catalog-dialog .youtube-viewport iframe")).to_have_count(1)
                assert page.evaluate("window.__ytCreates===1")
                page.locator(".catalog-dialog .catalog-close").click()
                page.locator("#youtube-dock button").filter(has_text="Fermer la vidéo").click()
                expect(page.locator(".youtube-viewport iframe")).to_have_count(0)
                # Changing stations destroys the old source and excerpts.
                page.evaluate('window.RadioNavigation.go("/")')
                expect(page.locator(".station-play")).to_have_count(2)
                page.locator(".station-play").nth(1).click()
                page.wait_for_function(
                    '() => document.querySelector("#radio-player").src!==window.__radioURL'
                )
                assert page.locator("#excerpt-player").get_attribute("src") is None
                # Admin uses real session + real persisted YouTube choice, no production data.
                page.evaluate('window.RadioNavigation.go("/admin")')
                expect(page.locator("#admin-login")).to_be_visible()
                page.locator("#admin-login input").fill("browser-test-only")
                page.locator("#admin-login button").click()
                expect(page).to_have_url(base + "/admin/dashboard")
                page.evaluate('window.RadioNavigation.go("/admin/catalogue")')
                expect(page.locator("#admin-catalog-results .admin-track")).to_have_count(2)
                page.locator("#admin-catalog-results .admin-track").first.click()
                expect(page.locator(".youtube-admin")).to_be_visible()
                page.locator(".youtube-admin button").filter(
                    has_text="Rechercher des vidéos"
                ).click()
                expect(page.locator(".youtube-admin .youtube-choices button")).to_have_count(4)
                page.locator(".youtube-admin .youtube-choices button").nth(1).click()
                expect(page.locator(".youtube-admin [role=status]")).to_contain_text("Choix manuel")
                page.evaluate('window.RadioNavigation.go("/admin/workers")')
                expect(page.locator(".worker-state")).to_have_count(3)
                page.screenshot(path=str(CAPTURES / f"workers-{width}.png"), full_page=True)
                # Language switch and preference survive reload + internal navigation.
                page.locator("#listening-settings-toggle").click()
                page.locator("#language-select").select_option("en")
                expect(page.locator("html")).to_have_attribute("lang", "en")
                expect(page.locator("h1")).to_have_text("Monitoring")
                page.wait_for_function("() => Boolean(window.RadioNavigation)")
                page.evaluate('window.RadioNavigation.go("/recherche")')
                expect(page.locator("h1")).to_have_text("Advanced search")
                page.screenshot(path=str(CAPTURES / f"search-english-{width}.png"), full_page=True)
                page.reload()
                expect(page.locator("html")).to_have_attribute("lang", "en")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                # Browser without state-preserving DOM moves: float from the outset.
                page.evaluate("Element.prototype.moveBefore = undefined")
                page.evaluate(
                    'window.TrackCatalog.open({title:"Song 0",artist:"Artist"}, {youtube:true})'
                )
                page.wait_for_function("() => window.__yt?.getPlayerState() === 1")
                expect(page.locator("#youtube-dock.is-floating")).to_be_visible()
                expect(page.locator(".catalog-dialog[open]")).to_have_count(0)
                page.evaluate(
                    'window.__fallbackFrame = document.querySelector(".youtube-viewport iframe")'
                )
                page.evaluate('window.RadioNavigation.go("/stats")')
                expect(page.locator("#stats-root")).to_be_visible()
                assert page.evaluate(
                    'document.querySelector(".youtube-viewport iframe") === window.__fallbackFrame'
                )
                assert not errors, errors
                context.close()
                print(f"{width}px: media, persistence, admin, monitoring, FR/EN OK", flush=True)
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
        log.close()
