"""Isolated native audio checks for finite preview queues and radio priority."""

import io
import os
import socket
import subprocess
import sys
import tempfile
import time
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


def silence(seconds):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 8000 * seconds)
    return output.getvalue()


with tempfile.TemporaryDirectory(prefix="radio-preview-sequence-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/test.db"
    os.environ["ADMIN_TOKEN"] = "browser-test-only"
    from app.database import SessionLocal, engine
    from app.models import Base, GlobalExclusion, Play, Station
    from app.services.catalog import ensure_entry
    from app.services.exclusions import exclusion_id, normalized_pair

    Base.metadata.create_all(engine)
    keys = {}
    with SessionLocal() as db:
        stations = [
            Station(
                name=f"Radio {i}",
                slug=f"radio-{i}",
                stream_url=f"https://example.test/radio{i}.wav",
            )
            for i in range(2)
        ]
        db.add_all(stations)
        db.flush()
        now = datetime.now(UTC)
        for index, title in enumerate(("A", "No preview", "Broken", "B", "C", "Hidden")):
            entry = ensure_entry(db, "Artist", title)
            keys[title] = entry.id
            entry.status = "matched"
            entry.selected = {
                "provider": "itunes",
                "provider_id": f"sequence-{index}",
                "artist": "Artist",
                "title": title,
                "preview_url": None
                if title == "No preview"
                else f"https://example.test/{title}.wav",
            }
            for count in range(6 - index):
                at = now - timedelta(minutes=index, seconds=count)
                db.add(
                    Play(
                        station_id=stations[0].id,
                        artist="Artist",
                        title=title,
                        detected_at=at,
                        last_seen_at=at,
                        source="json",
                        raw_metadata="synthetic",
                    )
                )
        artist, title = normalized_pair("Artist", "Hidden")
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
                context = browser.new_context(viewport={"width": width, "height": 900})
                context.route("**/api/stations/*/artwork", lambda r: r.fulfill(status=404))
                requests = []
                preview_wav, radio_wav = silence(10), silence(60)

                def media(
                    route, _request, requests=requests, radio_wav=radio_wav, preview_wav=preview_wav
                ):
                    name = Path(urlparse(route.request.url).path).stem
                    requests.append(name)
                    body = (
                        b"invalid audio"
                        if name == "Broken"
                        else (radio_wav if name.startswith("radio") else preview_wav)
                    )
                    route.fulfill(content_type="audio/wav", body=body)

                context.route("https://example.test/**", media)

                def excerpt(route, _request, requests=requests, preview_wav=preview_wav):
                    track_id = urlparse(route.request.url).path.split("/")[3]
                    title = next(title for title, key in keys.items() if key == track_id)
                    requests.append(title)
                    route.fulfill(
                        content_type="audio/wav",
                        body=b"invalid audio" if title == "Broken" else preview_wav,
                    )

                context.route("**/api/catalog/*/preview*", excerpt)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))

                def reset(path="/", page=page, requests=requests):
                    page.goto(base + path)
                    page.wait_for_function("() => Boolean(window.RadioPlayer)")
                    requests.clear()

                def playing(title, page=page):
                    expect(page.locator("#track-title")).to_have_text(title)
                    page.wait_for_function("""() => {
                        const a=document.querySelector('#excerpt-player');
                        return a && !a.paused && Number.isFinite(a.duration) && !a.error;
                    }""")
                    page.locator("#excerpt-player").evaluate("a=>a.playbackRate=1")
                    assert page.locator("#radio-player").evaluate("a=>a.paused || a.muted")

                def defer_next(page=page):
                    page.evaluate("""() => {
                        window.__fetch=window.MusicFilters.fetch;
                        window.MusicFilters.fetch=(url,...args)=>
                            url.startsWith('/api/explore?') && url.includes('track_id=')
                            ? new Promise(resolve=>{
                                window.__late=()=>resolve(new Response(JSON.stringify({items:[]})));
                              })
                            : window.__fetch(url,...args);
                    }""")

                def finish(page=page):
                    page.locator("#excerpt-player").evaluate("a=>a.playbackRate=16")

                def click_preview(title, page=page, playing=playing):
                    page.locator(f'.preview-button[data-preview-title="{title}"]').first.click()
                    playing(title)

                def start_radio(index=1, page=page):
                    page.locator(f'.station-play[data-id="{index}"]').click()
                    page.wait_for_function("""() => {
                        const a=document.querySelector('#radio-player');
                        return !a.paused && !a.muted;
                    }""")

                # Ranking order skips absent/failed previews, never wraps or retries a failed item.
                reset()
                click_preview("A")
                finish()
                playing("B")
                assert requests == ["A", "Broken", "B"], requests
                finish()
                playing("C")
                finish()
                expect(page.locator("#playback-label")).to_have_text("Fin des préécoutes")
                assert requests == ["A", "Broken", "B", "C"], requests
                assert page.locator("#excerpt-player").evaluate("a=>a.ended")
                # Starting halfway through the ranking only considers the following rows.
                click_preview("B")
                finish()
                playing("C")
                finish()
                expect(page.locator("#playback-label")).to_have_text("Fin des préécoutes")
                # Detail views retain the launch list and survive internal navigation.
                reset()
                page.locator(".podium-detail").first.click()
                page.locator(".preview-entry .preview-button").click()
                playing("A")
                page.locator(".catalog-close").click()
                page.evaluate("window.RadioNavigation.go('/recherche')")
                expect(page.locator("#explorer-form")).to_be_visible()
                finish()
                playing("B")
                # Advanced search order differs from the ranking: A, B, Broken, C, No preview.
                reset("/recherche?view=tracks&sort=title")
                click_preview("B")
                finish()
                playing("C")
                assert requests == ["B", "Broken", "C"], requests
                # Repetition statistics only chain the tracks in that displayed analysis page.
                reset("/stats")
                page.locator('[data-metric="repetitions"] > summary').click()
                expect(page.locator(".stats-repeat-track")).to_have_count(4)
                page.locator(".stats-repeat-track button").first.click()
                page.locator(".preview-entry .preview-button").click()
                playing("A")
                finish()
                playing("B")
                finish()
                expect(page.locator("#playback-label")).to_have_text("Fin des préécoutes")
                assert requests == ["A", "Broken", "B"]
                # Raw broadcast rows must not preview the same music repeatedly.
                reset("/recherche?view=plays&sort=newest")
                click_preview("A")
                finish()
                playing("B")
                assert requests.count("A") == 1
                # Quick search also supplies its visible order through the detail dialog.
                reset()
                page.evaluate("window.QuickSearch.open()")
                page.locator(".music-search-dialog input").fill("Artist")
                expect(page.locator(".music-search-result")).to_have_count(5)
                page.locator(".music-search-result").first.click()
                page.locator(".preview-entry .preview-button").click()
                playing("A")
                finish()
                playing("B")
                page.locator(".catalog-close").click()
                # Manual pause does not advance; resuming continues from that preview.
                reset()
                click_preview("A")
                page.locator("#player-toggle").click()
                expect(page.locator("#playback-label")).to_have_text("En pause")
                page.locator("#excerpt-player").dispatch_event("ended")
                assert requests == ["A"]
                page.locator("#player-toggle").click()
                playing("A")
                finish()
                playing("B")
                # An actively playing station takes priority without reloading its stream.
                reset()
                expect(page.locator("#resume-radio-after-preview")).to_be_checked()
                start_radio()
                radio_source = page.locator("#radio-player").get_attribute("src")
                click_preview("A")
                assert page.locator("#radio-player").evaluate("a=>!a.paused && a.muted")
                finish()
                expect(page.locator(".player")).to_have_attribute("data-mode", "radio")
                assert page.locator("#radio-player").evaluate("a=>!a.paused && !a.muted")
                assert page.locator("#radio-player").get_attribute("src") == radio_source
                assert requests == ["radio0", "A"], requests
                # Switching previews keeps the original radio priority for this listening session.
                click_preview("A")
                click_preview("B")
                finish()
                expect(page.locator(".player")).to_have_attribute("data-mode", "radio")
                assert requests[-2:] == ["A", "B"]
                # Failure never resumes the radio or starts another preview automatically.
                click_preview("A")
                page.locator('.preview-button[data-preview-title="Broken"]').first.click()
                expect(page.locator("#playback-label")).to_have_text("Lecture indisponible")
                assert page.locator("#radio-player").evaluate("a=>!a.paused && a.muted")
                assert requests[-1] == "Broken"
                start_radio()
                # A paused station is not a return target; its connection stays silent.
                page.locator("#player-toggle").click()
                click_preview("A")
                finish()
                playing("B")
                assert page.locator("#radio-player").evaluate("a=>!a.paused && a.muted")
                # Explicitly turning radio return off is preserved across reloads and stops there.
                reset()
                page.locator("#listening-settings-toggle").click()
                page.locator("#resume-radio-after-preview").uncheck()
                page.keyboard.press("Escape")
                reset()
                expect(page.locator("#resume-radio-after-preview")).not_to_be_checked()
                start_radio()
                click_preview("A")
                finish()
                expect(page.locator("#playback-label")).to_have_text("Extrait terminé")
                assert page.locator("#radio-player").evaluate("a=>!a.paused && a.muted")
                assert requests == ["radio0", "A"]
                # A pending validation cannot override pause or a newly chosen station.
                reset()
                defer_next()
                click_preview("A")
                finish()
                page.wait_for_function("() => Boolean(window.__late)")
                page.locator('.preview-button[data-preview-title="A"]').first.click()
                expect(page.locator("#playback-label")).to_have_text("En pause")
                page.evaluate("window.__late();window.MusicFilters.fetch=window.__fetch")
                assert requests == ["A"]
                start_radio(2)
                expect(page.locator("#excerpt-player")).not_to_have_attribute("src")
                # A station choice also cancels an outstanding transition without needing a pause.
                reset()
                defer_next()
                click_preview("A")
                finish()
                page.wait_for_function("() => Boolean(window.__late)")
                start_radio(2)
                page.evaluate("window.__late();window.MusicFilters.fetch=window.__fetch")
                expect(page.locator(".player")).to_have_attribute("data-mode", "radio")
                assert requests == ["A", "radio1"]
                # Station changes pause, detach and unload the previous stream.
                page.evaluate("""() => {
                    const a=document.querySelector('#radio-player');window.__changes=[];
                    for(const method of ['pause','removeAttribute','load']) {
                        const original=a[method].bind(a);
                        a[method]=(...args)=>{
                            window.__changes.push([method,...args]);return original(...args);
                        };
                    }
                }""")
                start_radio(1)
                changes = page.evaluate("window.__changes")
                assert (
                    ["pause"] in changes
                    and ["removeAttribute", "src"] in changes
                    and ["load"] in changes
                )
                # Video playback supersedes a transition whose validation response arrives late.
                reset()
                defer_next()
                click_preview("A")
                finish()
                page.wait_for_function("() => Boolean(window.__late)")
                page.evaluate("""() => {
                    window.RadioPlayer.startVideo({
                        playing:()=>false, play:()=>{}, pause:()=>{}, setVolume:()=>{},
                        volume:()=>1, muted:()=>false, duration:()=>0, position:()=>0
                    }, {title:'Video fixture',artist:'Artist'});
                    window.__late();window.MusicFilters.fetch=window.__fetch;
                }""")
                expect(page.locator(".player")).to_have_attribute("data-mode", "video")
                assert page.locator("#excerpt-player").evaluate("a=>a.paused")
                assert requests == ["A"]
                # A browser autoplay refusal stops instead of attempting every subsequent source.
                reset()
                click_preview("A")
                page.locator("#excerpt-player").evaluate("""a => {
                    a.play=()=>Promise.reject(new DOMException('Blocked','NotAllowedError'));
                }""")
                finish()
                expect(page.locator("#playback-label")).to_have_text("Lecture indisponible")
                assert requests.count("A") == 1 and requests.count("Broken") <= 1
                assert "B" not in requests and "C" not in requests
                # A filter change cancels the captured queue and clears hidden playback metadata.
                reset()
                click_preview("A")
                page.evaluate("window.MusicFilters.hide({artist:'Artist',title:'A'},false)")
                expect(page.locator("#playback-label")).to_have_text("Préécoute arrêtée")
                expect(page.locator("#excerpt-player")).not_to_have_attribute("src")
                expect(page.locator("#track-title")).not_to_have_text("A")
                assert requests == ["A"]
                # A transport failure while revalidating the next item stops without a retry loop.
                reset()
                page.route("**/api/explore?*track_id=*", lambda route: route.fulfill(status=503))
                click_preview("B")
                finish()
                expect(page.locator("#player-status")).to_contain_text("Impossible de vérifier")
                assert requests == ["B"]
                Path("/tmp/radio-preview-sequence").mkdir(exist_ok=True)
                page.screenshot(path=f"/tmp/radio-preview-sequence/{width}.png")
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                assert not errors, errors
                context.close()
                print(
                    f"{width}px: queue contexts, native endings/errors, "
                    "pause, radio priority, filters OK",
                    flush=True,
                )
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
