"""Isolated video resizing, simplified controls and dialog transitions."""

import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
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
            played_at = datetime.now(UTC) - timedelta(minutes=1)
            for _ in range(i + 1):
                db.add(
                    Play(
                        station_id=station.id,
                        artist="Artist",
                        title=f"Song {i}",
                        source="icy",
                        raw_metadata="original",
                        detected_at=played_at,
                        last_seen_at=played_at,
                    )
                )
        db.add(Station(name="Empty Radio", slug="empty", stream_url="https://example.test/live"))
        db.commit()
    from app.services.prepared_rankings import refresh

    refresh(SessionLocal)
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
            for width, height in ((1280, 900), (414, 846), (320, 846), (846, 390)):
                context = browser.new_context(
                    viewport={"width": width, "height": height}, has_touch=width <= 760
                )
                context.route("**/api/stations/*/artwork", lambda route: route.fulfill(status=404))
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
window.__creates=0;window.__destroys=0;
window.YT={Player:function(frame,options){
 window.__creates++;let state=0,volume=100,muted=false;
 this.playVideo=()=>{state=1;options.events.onStateChange({data:1});};
 this.pauseVideo=()=>{state=2;options.events.onStateChange({data:2});};
 this.getPlayerState=()=>state;this.getDuration=()=>240;this.getCurrentTime=()=>30;
 this.seekTo=()=>{};this.getVolume=()=>volume;this.setVolume=v=>{volume=v;};
 this.mute=()=>{muted=true;};this.unMute=()=>{muted=false;};this.isMuted=()=>muted;
 this.destroy=()=>{window.__destroys++;frame.remove();};
 window.__video=this;window.__events=options.events;
 setTimeout(()=>options.events.onReady({target:this}),0);
}};window.onYouTubeIframeAPIReady();
""",
                    ),
                )
                mode = ["matched"]

                def videos(route, _request, mode=mode):
                    if mode[0] == "error":
                        return route.fulfill(
                            status=429,
                            json={
                                "detail": "Limite de recherches YouTube atteinte "
                                "ou service en temporisation."
                            },
                        )
                    candidates = (
                        []
                        if mode[0] == "not_found"
                        else [{"id": "abcdefghijk", "title": "Song official", "channel": "Artist"}]
                    )
                    if mode[0] == "ambiguous":
                        candidates.append(
                            {"id": "ABCDEFGHIJK", "title": "Song audio", "channel": "Artist"}
                        )
                    route.fulfill(json={"status": mode[0], "candidates": candidates})

                context.route("**/api/catalog/*/youtube**", videos)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                page.goto(base + "/")
                page.locator(".podium-detail").first.click()
                expect(page.locator(".preview-entry .youtube-button")).to_have_count(1)
                expect(page.locator(".catalog-platforms .youtube-button")).to_have_count(0)
                assert page.locator('script[src*="youtube.com"]').count() == 0
                page.locator(".preview-entry .youtube-button").click()
                page.wait_for_function("() => window.__video?.getPlayerState()===1")
                expect(page.locator("#youtube-dock [role=status]")).to_be_empty()
                assert not page.locator("#youtube-dock .hint").count()
                assert not page.get_by_role("button", name="Plein écran", exact=True).count()
                assert not page.get_by_role("button", name="Réduire la vidéo", exact=True).count()
                page.evaluate("window.__frame=document.querySelector('.youtube-viewport iframe')")
                assert "controls=0" in page.locator(".youtube-viewport iframe").get_attribute("src")
                grip = page.get_by_role("slider", name="Taille de la vidéo")
                grip.focus()
                grip.press("Home")
                narrow = page.locator("#youtube-dock").bounding_box()["width"]
                grip.press("ArrowRight")
                wider = page.locator("#youtube-dock").bounding_box()["width"]
                assert wider > narrow
                grip.press("End")
                widest = page.locator("#youtube-dock").bounding_box()["width"]
                assert widest >= wider
                page.locator(".youtube-size").click()
                assert page.locator("#youtube-dock").bounding_box()["width"] < widest
                page.locator(".youtube-size").click()
                assert page.locator("#youtube-dock").bounding_box()["width"] == widest
                if width == 1280:
                    grip.press("Home")
                    grip.scroll_into_view_if_needed()
                    box = grip.bounding_box()
                    page.mouse.move(box["x"] + 12, box["y"] + 12)
                    page.mouse.down()
                    page.mouse.move(box["x"] + 76, box["y"] + 12, steps=5)
                    page.mouse.up()
                    assert page.locator("#youtube-dock").bounding_box()["width"] > narrow + 50
                if width <= 760:
                    grip.press("Home")
                    grip.scroll_into_view_if_needed()
                    touch_box = grip.bounding_box()
                    touch_x, touch_y = touch_box["x"] + 12, touch_box["y"] + 12
                    touch = context.new_cdp_session(page)
                    touch.send(
                        "Input.dispatchTouchEvent",
                        {"type": "touchStart", "touchPoints": [{"x": touch_x, "y": touch_y}]},
                    )
                    touch.send(
                        "Input.dispatchTouchEvent",
                        {"type": "touchMove", "touchPoints": [{"x": touch_x + 12, "y": touch_y}]},
                    )
                    touch.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
                    assert page.locator("#youtube-dock").bounding_box()["width"] > narrow
                    touch.detach()
                page.locator(".catalog-close").click()
                expect(page.locator("#youtube-dock.is-floating")).to_be_visible()
                grip.focus()
                grip.press("Home")
                compact = page.locator("#youtube-dock").bounding_box()["width"]
                grip.press("End")
                assert page.locator("#youtube-dock").bounding_box()["width"] >= compact
                dock_box = page.locator("#youtube-dock").bounding_box()
                assert dock_box["x"] >= 0 and dock_box["x"] + dock_box["width"] <= width
                assert dock_box["y"] >= 0 and dock_box["y"] + dock_box["height"] <= height
                assert (
                    page.locator(".youtube-size").bounding_box()["x"]
                    < page.locator(".youtube-close").bounding_box()["x"]
                )
                grip.press("Home")
                dock = page.locator("#youtube-dock")
                expect(page.locator(".youtube-move")).to_have_count(0)
                dock.focus()
                page.keyboard.press("Home")
                before_move = dock.bounding_box()
                left = before_move["x"]
                assert left == 12
                if width == 1280:
                    viewport = page.locator(".youtube-viewport").bounding_box()
                    x, y = (
                        viewport["x"] + viewport["width"] / 2,
                        viewport["y"] + viewport["height"] / 2,
                    )
                    page.mouse.move(x, y)
                    page.mouse.down()
                    page.wait_for_timeout(350)
                    page.mouse.move(x + 60, y + 35, steps=5)
                    page.mouse.up()
                    after_move = dock.bounding_box()
                    assert after_move["x"] > before_move["x"] and after_move["y"] > before_move["y"]
                if width <= 760:
                    viewport = page.locator(".youtube-viewport").bounding_box()
                    x, y = (
                        viewport["x"] + viewport["width"] / 2,
                        viewport["y"] + viewport["height"] / 2,
                    )
                    touch = context.new_cdp_session(page)
                    touch.send(
                        "Input.dispatchTouchEvent",
                        {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]},
                    )
                    page.wait_for_timeout(350)
                    touch.send(
                        "Input.dispatchTouchEvent",
                        {"type": "touchMove", "touchPoints": [{"x": x + 24, "y": y + 24}]},
                    )
                    touch.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
                    after_move = dock.bounding_box()
                    assert after_move["x"] > before_move["x"] and after_move["y"] > before_move["y"]
                    touch.detach()
                dimensions = page.locator("#youtube-dock").evaluate(
                    "e => [e.scrollHeight, e.clientHeight]"
                )
                assert dimensions[0] <= dimensions[1] + 1, dimensions
                expect(page.locator("#youtube-dock")).to_have_css("overflow-y", "hidden")
                grip.press("End")
                frame_box = page.locator(".youtube-viewport iframe").bounding_box()
                assert frame_box["width"] >= 200 and frame_box["height"] >= 200
                if width == 1280:
                    page.mouse.move(0, 0)
                    page.locator("h1").click()
                    expect(page.locator(".youtube-close")).to_have_css("opacity", "0")
                    page.locator("#youtube-dock").hover(position={"x": 4, "y": 4})
                    page.locator(".youtube-viewport").hover()
                    expect(page.locator(".youtube-close")).to_have_css("opacity", "1")
                Path("/tmp/radio-youtube-phase2").mkdir(exist_ok=True)
                for theme in ("light", "dark"):
                    page.evaluate("t=>document.documentElement.dataset.theme=t", theme)
                    page.screenshot(path=f"/tmp/radio-youtube-phase2/{width}-{theme}.png")
                page.evaluate("window.RadioNavigation.go('/recherche')")
                expect(page.locator("#explorer-form")).to_be_visible()
                assert page.evaluate(
                    "document.querySelector('.youtube-viewport iframe')===window.__frame"
                    " && window.__creates===1 && window.__destroys===0"
                )
                page.evaluate("window.TrackCatalog.open({artist:'Artist',title:'Song 2'})")
                expect(page.locator(".catalog-dialog .youtube-viewport iframe")).to_have_count(1)
                assert page.evaluate(
                    "document.querySelector('.youtube-viewport iframe')===window.__frame"
                )
                page.get_by_role("button", name="Fermer la vidéo", exact=True).focus()
                page.keyboard.press("Enter")
                expect(page.locator("#youtube-dock")).not_to_be_visible()
                expect(page.locator(".preview-entry .youtube-button")).to_be_focused()
                for state in ("error", "not_found", "ambiguous", "rejected"):
                    mode[0] = state
                    page.locator(".preview-entry .youtube-button").click()
                    expect(page.locator("#youtube-dock [role=status]")).not_to_be_empty()
                    assert page.locator("#youtube-dock iframe").count() == 0
                    page.keyboard.press("Escape")
                    expect(page.locator("#youtube-dock")).not_to_be_visible()
                    page.evaluate("window.TrackCatalog.open({artist:'Artist',title:'Song 2'})")
                    expect(page.locator(".preview-entry .youtube-button")).to_have_count(1)
                # A late response after closing a pending search must not resurrect its dock.
                page.evaluate("""() => {
                  window.__fetch=window.MusicFilters.fetch;
                  window.MusicFilters.fetch=(url,...args)=>url.includes('/youtube?')
                    ? new Promise(resolve=>window.__late=()=>resolve(new Response(JSON.stringify({
                        status:'matched',
                        candidates:[{id:'abcdefghijk',title:'Late',channel:'Artist'}]
                      }))))
                    : window.__fetch(url,...args);
                }""")
                page.locator(".preview-entry .youtube-button").click()
                page.wait_for_function("() => Boolean(window.__late)")
                page.locator(".catalog-close").click()
                page.evaluate("window.__late();window.MusicFilters.fetch=window.__fetch")
                expect(page.locator("#youtube-dock")).not_to_be_visible()
                assert page.locator("#youtube-dock iframe").count() == 0
                # Closing an unplayable iframe clears it; a real loaded video remains persistent.
                mode[0] = "matched"
                page.evaluate(
                    "window.TrackCatalog.open({artist:'Artist',title:'Song 2'}, {youtube:true})"
                )
                page.wait_for_function(
                    "() => window.__video.getPlayerState()===1 && "
                    "document.querySelector('.youtube-viewport iframe')"
                )
                page.evaluate("window.__events.onError({data:150})")
                expect(page.locator("#youtube-dock [role=status]")).to_contain_text("ne peut pas")
                page.locator(".catalog-close").click()
                expect(page.locator("#youtube-dock")).not_to_be_visible()
                page.evaluate("Element.prototype.moveBefore=undefined")
                mode[0] = "error"
                page.evaluate(
                    "window.TrackCatalog.open({artist:'Artist',title:'Song 2'}, {youtube:true})"
                )
                expect(page.locator(".catalog-dialog #youtube-dock [role=status]")).to_contain_text(
                    "Limite"
                )
                page.locator(".catalog-close").click()
                expect(page.locator("#youtube-dock")).not_to_be_visible()
                mode[0] = "matched"
                page.evaluate(
                    "window.TrackCatalog.open({artist:'Artist',title:'Song 2'}, {youtube:true})"
                )
                expect(page.locator("#youtube-dock.is-floating iframe")).to_have_count(1)
                expect(page.locator(".catalog-dialog[open]")).to_have_count(0)
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                assert not errors, errors
                context.close()
                print(
                    f"{width}x{height}: resize, controls, pending/errors, "
                    "choices, persistence and fallback OK",
                    flush=True,
                )
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
