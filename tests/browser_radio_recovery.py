"""Native audio recovery, bounded retries and cancellation in an isolated browser."""

import io
import os
import socket
import subprocess
import sys
import tempfile
import time
import wave
from datetime import UTC, datetime

from playwright.sync_api import expect, sync_playwright


def silence():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 8000 * 120)
    return output.getvalue()


with tempfile.TemporaryDirectory(prefix="radio-recovery-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/test.db"
    from app.database import SessionLocal, engine
    from app.models import Base, Station

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.add_all(
            Station(
                name=f"Radio {i}", slug=f"radio-{i}", stream_url=f"https://example.test/{i}.wav"
            )
            for i in range(2)
        )
        db.commit()
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
            wav = silence()
            for width in (1280, 414, 320):
                context = browser.new_context(viewport={"width": width, "height": 900})
                context.route("**/api/stations/*/artwork", lambda r: r.fulfill(status=404))
                requests, errors, pending = [], [], []
                state = {"broken": True, "recover": True, "hang": False}

                def media(route, _request, state=state, requests=requests, pending=pending):
                    requests.append(route.request.url)
                    if state["hang"]:
                        state["hang"] = False
                        pending.append(route)
                        return  # An upstream that connects but never delivers audio.
                    broken = state["broken"]
                    if state["recover"]:
                        state["broken"] = False
                    route.fulfill(content_type="audio/wav", body=b"broken" if broken else wav)

                context.route("https://example.test/**", media)
                page = context.new_page()
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                page.clock.install(time=datetime(2026, 11, 9, 12, 0, tzinfo=UTC))
                page.clock.pause_at(datetime(2026, 11, 9, 12, 0, 1, tzinfo=UTC))
                page.goto(base)
                first, second = page.locator(".station-play").all()
                first.click()
                expect(page.locator("#player-status")).to_contain_text("nouvel essai")
                assert len(requests) == 1
                page.clock.run_for(1100)
                page.wait_for_function(
                    "() => { const a = document.querySelector('#radio-player'); "
                    "return !a.paused && !a.error && a.readyState >= 3; }"
                )
                assert len(requests) == 2
                page.clock.run_for(1000)
                assert page.locator("#radio-player").evaluate("a => a.currentTime > 0")
                # Pause/resume and internal navigation keep the healthy connection.
                page.locator("#player-toggle").click()
                assert page.locator("#radio-player").evaluate("a => a.muted && !a.paused")
                page.clock.run_for(15000)
                assert len(requests) == 2
                page.locator("#player-toggle").click()
                page.evaluate("window.RadioNavigation.go('/radios')")
                expect(page.locator("#station-search")).to_be_visible()
                assert len(requests) == 2
                # Global shortcuts respect typing and native control activation.
                page.locator("body").evaluate("e => { e.tabIndex = -1; e.focus(); }")
                page.keyboard.press("Space")
                assert page.locator("#radio-player").evaluate("a => a.muted && !a.paused")
                page.keyboard.press("Space")
                assert page.locator("#radio-player").evaluate("a => !a.muted && !a.paused")
                page.locator("#player-volume").evaluate(
                    "e => { e.value = .5; e.dispatchEvent(new Event('input')); }"
                )
                page.keyboard.press("q")
                assert page.locator("#player-volume").input_value() == "0.48"
                page.keyboard.press("D")
                assert page.locator("#player-volume").input_value() == "0.5"
                for _ in range(60):
                    page.keyboard.press("d")
                assert page.locator("#player-volume").input_value() == "1"
                for _ in range(60):
                    page.keyboard.press("q")
                assert page.locator("#player-volume").input_value() == "0"
                page.keyboard.press("d")
                assert page.locator("#radio-player").evaluate("a => a.volume === .02 && !a.muted")
                search = page.locator("#station-search")
                search.focus()
                search.press_sequentially("q d ")
                assert search.input_value() == "q d "
                assert page.locator("#player-volume").input_value() == "0.02"
                search.fill("")
                page.locator("#player-toggle").focus()
                page.keyboard.press("Space")
                assert page.locator("#radio-player").evaluate("a => a.muted")
                page.keyboard.press("Space")
                assert page.locator("#radio-player").evaluate("a => !a.muted")
                page.locator("body").focus()
                page.keyboard.press("Control+q")
                assert page.locator("#player-volume").input_value() == "0.02"
                # Holding space consumes repeats without toggling repeatedly or scrolling.
                y = page.evaluate("scrollY")
                page.keyboard.down("Space")
                page.keyboard.down("Space")
                page.keyboard.up("Space")
                assert page.locator("#radio-player").evaluate("a => a.muted")
                assert page.evaluate("scrollY") == y
                page.keyboard.press("Space")
                print(width, "raccourcis / bornes / saisie / contrôles : OK", flush=True)
                # A permanently broken stream stops after exactly three retries.
                state.update(broken=True, recover=False)
                page.locator(".station-play").nth(1).click()
                expect(page.locator("#player-status")).to_contain_text("nouvel essai")
                for delay in (1100, 2100, 4100):
                    page.clock.run_for(delay)
                    expect(page.locator("#player-status")).to_contain_text(
                        "trois essais" if delay == 4100 else "nouvel essai"
                    )
                exhausted = len(requests)
                assert exhausted == 6  # First station: 2; broken second station: 1 + 3.
                page.clock.run_for(60000)
                assert len(requests) == exhausted
                # Explicit user retry really reloads the same failed source.
                state.update(broken=False)
                page.locator("#player-toggle").click()
                page.wait_for_function(
                    "() => { const a = document.querySelector('#radio-player'); "
                    "return !a.paused && !a.error && a.readyState >= 3; }"
                )
                assert len(requests) == exhausted + 1
                # Switching stations cancels a retry scheduled for the old URL.
                state.update(broken=True)
                page.locator(".station-play").nth(0).click()
                expect(page.locator("#player-status")).to_contain_text("nouvel essai")
                state.update(broken=False)
                page.locator(".station-play").nth(1).click()
                page.wait_for_function(
                    "() => document.querySelector('#radio-player').readyState >= 3"
                )
                before = len(requests)
                page.clock.run_for(15000)
                assert len(requests) == before
                assert page.locator("#radio-player").evaluate("a => a.src.endsWith('/1.wav')")
                # A hanging connection gets one watchdog, even with repeated waiting events.
                state.update(hang=True)
                page.locator(".station-play").nth(0).click()
                for _ in range(100):
                    if pending:
                        break
                    page.wait_for_timeout(10)
                assert len(pending) == 1
                before = len(requests)
                page.locator("#radio-player").evaluate(
                    "a => { a.dispatchEvent(new Event('waiting')); "
                    "a.dispatchEvent(new Event('stalled')); }"
                )
                page.clock.run_for(12000)
                expect(page.locator("#player-status")).to_contain_text("nouvel essai")
                page.clock.run_for(1100)
                page.wait_for_function(
                    "() => document.querySelector('#radio-player').readyState >= 3"
                )
                assert len(requests) == before + 1, (before, requests)
                pending.pop().abort()
                # An explicit pause cancels a pending reconnect.
                state.update(broken=True)
                page.locator(".station-play").nth(1).click()
                expect(page.locator("#player-status")).to_contain_text("nouvel essai")
                page.evaluate("window.RadioPlayer.pause()")
                before = len(requests)
                page.clock.run_for(15000)
                assert len(requests) == before
                # Switching to an excerpt also cancels retries and preserves its controls.
                state.update(broken=True)
                page.locator(".station-play").nth(0).click()
                expect(page.locator("#player-status")).to_contain_text("nouvel essai")
                state.update(broken=False)
                page.evaluate(
                    "window.RadioPlayer.preview({artist:'Artist',title:'Preview'},"
                    "'https://example.test/preview.wav')"
                )
                page.wait_for_function(
                    "() => document.querySelector('#excerpt-player').readyState >= 3"
                )
                before = len(requests)
                page.clock.run_for(8000)
                assert len(requests) == before
                assert page.locator(".player").get_attribute("data-mode") == "preview"
                page.locator("body").focus()
                page.keyboard.press("Space")
                assert page.locator("#excerpt-player").evaluate("a => a.paused")
                page.keyboard.press("Space")
                page.wait_for_function("() => !document.querySelector('#excerpt-player').paused")
                page.keyboard.press("q")
                assert page.locator("#excerpt-player").evaluate("a => a.volume === 0")
                page.keyboard.press("d")
                assert page.locator("#excerpt-player").evaluate("a => a.volume === .02")
                page.evaluate(
                    "() => { const e = document.createElement('div'); "
                    "e.contentEditable = 'true'; e.id = 'editing-fixture'; "
                    "document.body.append(e); e.focus(); }"
                )
                page.keyboard.type("q d ")
                assert (
                    page.locator("#editing-fixture").inner_text().replace("\u00a0", " ") == "q d "
                )
                assert page.locator("#player-volume").input_value() == "0.02"
                assert not errors, errors
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                print(
                    width,
                    "reprise / 3 essais / retry manuel / annulations / navigation : OK",
                    flush=True,
                )
                context.close()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
