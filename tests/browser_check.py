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

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="radio-browser-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/browser.db"
    os.environ["ADMIN_TOKEN"] = "browser-test-only"
    from app.database import SessionLocal, engine
    from app.models import Base, Play, Station
    from app.services.catalog import ensure_entry

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        station = Station(name="Radio Test", slug="test", stream_url="https://example.com/live")
        db.add(station)
        db.flush()
        for i, title in enumerate(
            ["Soon (ft Gaetan Roussel)", "Track Two", "Track Three", "Track Four", "Track Five"]
        ):
            now = datetime.now(UTC) - timedelta(minutes=i)
            db.add(
                Play(
                    station_id=station.id,
                    artist="Mosimann",
                    title=title,
                    raw_metadata=title,
                    source="icy",
                    detected_at=now,
                    last_seen_at=now,
                )
            )
            entry = ensure_entry(db, "Mosimann", title)
            entry.status = "matched"
            entry.selected = {
                "provider": "deezer",
                "provider_id": str(i + 1),
                "title": title,
                "artist": "Mosimann",
                "duration": 151,
                "album": "Album Test",
                "genres": ["Pop", "Dance"],
                "genre_source": "deezer",
                "release_date": "2026-01-01",
                "isrc": f"FRTEST{i}",
                "cover_url": "https://cdn-images.dzcdn.net/test-cover.svg"
                if i != 3
                else "https://cdn-images.dzcdn.net/broken.svg",
                "preview_url": "https://cdnt-preview.dzcdn.net/test.mp3",
                "url": "https://www.deezer.com/track/1",
            }
            entry.primary_genre = "Pop"
            entry.candidates = [dict(entry.selected)]
        for offset in (6, 8):
            at = datetime.now(UTC) - timedelta(minutes=offset)
            db.add(
                Play(
                    station_id=station.id,
                    artist="Mosimann",
                    title="Soon (ft Gaetan Roussel)",
                    raw_metadata="Soon",
                    source="icy",
                    detected_at=at,
                    last_seen_at=at,
                )
            )
        db.commit()
    log = open(f"{directory}/server.log", "w")
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
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
        cwd=root,
        env=os.environ,
        stdout=log,
        stderr=log,
    )
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.1)
        audio_buffer = io.BytesIO()
        with wave.open(audio_buffer, "wb") as sound:
            sound.setnchannels(1)
            sound.setsampwidth(2)
            sound.setframerate(8000)
            sound.writeframes(b"\x00\x00" * 8000 * 120)
        with sync_playwright() as p:
            engines = os.getenv("RADIO_BROWSER_ENGINES", "chromium").split(",")
            if not set(engines) <= {"chromium", "webkit", "firefox"}:
                raise ValueError("RADIO_BROWSER_ENGINES must name Playwright browser engines")
            browsers = [getattr(p, name).launch() for name in engines]
            try:
                for browser in browsers:
                    for width, height in [(1280, 900), (320, 740), (402, 874), (412, 915)]:
                        context = browser.new_context(viewport={"width": width, "height": height})
                        page = context.new_page()
                        errors = []
                        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))

                        def audio_response(route):
                            body = audio_buffer.getvalue()
                            headers = {"Accept-Ranges": "bytes"}
                            requested = route.request.headers.get("range")
                            if requested:
                                start, _, end = requested.removeprefix("bytes=").partition("-")
                                first = int(start or 0)
                                last = min(int(end) if end else len(body) - 1, len(body) - 1)
                                headers["Content-Range"] = f"bytes {first}-{last}/{len(body)}"
                                route.fulfill(
                                    status=206,
                                    content_type="audio/wav",
                                    headers=headers,
                                    body=body[first : last + 1],
                                )
                            else:
                                route.fulfill(content_type="audio/wav", headers=headers, body=body)

                        page.route("https://example.com/live", audio_response)

                        def cover(route):
                            if "broken.svg" in route.request.url:
                                route.fulfill(status=404, body="Missing artwork")
                            else:
                                route.fulfill(
                                    content_type="image/svg+xml",
                                    body=(
                                        '<svg xmlns="http://www.w3.org/2000/svg" '
                                        'width="500" height="500"><rect width="500" '
                                        'height="500" fill="#e82d49"/></svg>'
                                    ),
                                )

                        page.route("https://cdn-images.dzcdn.net/**", cover)
                        page.route("**/api/stations/*/artwork", cover)
                        page.route(
                            "**/api/catalog/spotify-match?**",
                            lambda route: route.fulfill(
                                json={
                                    "status": "found",
                                    "track": {
                                        "title": "Uber",
                                        "artist": "KeBlack, Franglish, Mauvais Djo",
                                        "url": "https://open.spotify.com/track/7DstNl9Mumkt3ujs7V84qq",
                                    },
                                }
                            ),
                        )
                        page.goto(base_url + "/")
                        expect(page).to_have_title("Classements · Radio Tracker")
                        if width <= 760:
                            page.locator("#mobile-nav-toggle").click()
                        expect(
                            page.get_by_role("link", name="Radio Tracker", exact=True)
                        ).to_be_visible()
                        if width <= 760:
                            page.keyboard.press("Escape")
                        page.wait_for_function(
                            "document.querySelector('.brand-mark').naturalWidth > 0"
                        )
                        for selector in ['link[rel="icon"]', 'link[rel="apple-touch-icon"]']:
                            for icon in page.locator(selector).all():
                                resource = context.request.get(
                                    base_url + icon.get_attribute("href")
                                )
                                assert resource.ok and resource.headers["content-type"].startswith(
                                    "image/"
                                )
                        expect(page.locator(".podium-card")).to_have_count(3)
                        expect(page.locator(".cover-rank")).to_have_text(["01", "02", "03"])
                        # Include loaded artwork and a failed image with its fallback.
                        page.locator(".podium").scroll_into_view_if_needed()
                        expect(page.locator(".cover-2 .cover-image")).to_have_count(0)
                        geometry = page.locator(".podium-card .cover").evaluate_all(
                            """nodes => nodes.every(e => {
                            const rect = e.getBoundingClientRect();
                            const rank = e.querySelector('.cover-rank').getBoundingClientRect();
                            return rect.right <= innerWidth && rect.left >= 0
                                && Math.abs(rect.width-rect.height) < 1
                                && rank.left >= rect.left && rank.right <= rect.right
                                && rank.top >= rect.top && rank.bottom <= rect.bottom;
                        })"""
                        )
                        assert geometry, (browser.browser_type.name, width, "cover/rank overflow")
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= innerWidth"
                        ), width
                        station_button = page.locator(".station-play")
                        station_button.scroll_into_view_if_needed()
                        page.wait_for_function(
                            "document.querySelector('.station-logo')?.naturalWidth > 0"
                        )
                        station_button.click()
                        page.wait_for_function(
                            """() => {
                                const player = document.querySelector('#radio-player');
                                return !player.paused && player.currentTime > 0.1;
                            }"""
                        )
                        expect(page.locator(".player-cover img")).to_have_attribute(
                            "src", "https://cdn-images.dzcdn.net/test-cover.svg"
                        )
                        expect(station_button.locator("span")).to_have_text("Ⅱ")
                        # A broken station image restores the plain playback button.
                        station_button.locator("img").evaluate(
                            "img => { img.src = 'https://cdn-images.dzcdn.net/broken.svg'; }"
                        )
                        expect(station_button.locator("img")).to_have_count(0)
                        expect(station_button.locator("span")).to_have_text("Ⅱ")
                        page.evaluate("""() => {
                            window.retainedPlayer = document.querySelector('#radio-player');
                            window.pauseEvents = 0;
                            window.loadEvents = 0;
                            retainedPlayer.addEventListener('pause', () => pauseEvents++);
                            retainedPlayer.addEventListener('loadstart', () => loadEvents++);
                            window.initialTime = retainedPlayer.currentTime;
                        }""")

                        def assert_uninterrupted(page=page, width=width):
                            assert page.evaluate("""() =>
                                document.querySelector('#radio-player') === window.retainedPlayer
                                && !retainedPlayer.paused
                                && retainedPlayer.currentTime >= initialTime
                                && pauseEvents === 0 && loadEvents === 0"""), (
                                width,
                                "audio interrupted",
                            )
                            assert page.evaluate(
                                "document.documentElement.scrollWidth <= innerWidth"
                            ), width

                        navigation = page.get_by_role("navigation", name="Navigation principale")
                        if width <= 760:
                            page.locator("#mobile-nav-toggle").click()
                        navigation.get_by_role("link", name="Stats", exact=True).click()
                        expect(page.locator("h1")).to_have_text("Stats")
                        page.locator('[data-metric="diversity"] > summary').click()
                        expect(page.locator('[data-metric="diversity"] tbody tr')).to_have_count(1)
                        page.locator('[data-metric="repetitions"] > summary').click()
                        expect(page.locator('[data-metric="repetitions"] tbody tr')).to_have_count(
                            1
                        )
                        page.locator('[data-metric="repetitions"] .cell-link').first.click()
                        expect(page.locator(".catalog-dialog .hourly-chart meter")).to_have_count(
                            24
                        )
                        page.screenshot(
                            path=f"/tmp/radio-track-stats-{browser.browser_type.name}-{width}.png"
                        )
                        page.get_by_role("button", name="Masquer pour moi", exact=True).click()
                        expect(
                            page.locator('[data-metric="diversity"] tbody td').nth(1)
                        ).to_have_text("4")
                        expect(
                            page.locator('[data-metric="diversity"] tbody td').nth(2)
                        ).to_have_text("4")
                        page.locator("[data-open-exclusions]").click()
                        page.get_by_role("button", name="Retirer de ma liste", exact=True).click()
                        page.keyboard.press("Escape")
                        expect(
                            page.locator('[data-metric="diversity"] tbody td').nth(1)
                        ).to_have_text("5")
                        expect(
                            page.locator('[data-metric="diversity"] tbody td').nth(2)
                        ).to_have_text("7")
                        page.locator("#repetition-hours").select_option("6")
                        expect(
                            page.locator('[data-metric="repetitions"] [role="status"]')
                        ).to_contain_text("Du ")
                        page.screenshot(
                            path=f"/tmp/radio-stats-{browser.browser_type.name}-{width}.png"
                        )
                        assert_uninterrupted()
                        page.evaluate(
                            """() => {
                                const link = document.createElement('a'); link.href='/admin';
                                document.body.append(link); link.click(); link.remove();
                            }"""
                        )
                        expect(page.locator("#admin-login")).to_be_visible()
                        page.locator('[name="token"]').fill("browser-test-only")
                        page.get_by_role("button", name="Ouvrir l’administration").click()
                        expect(page.locator("#admin-workspace")).to_be_visible()
                        expect(page.locator('[name="token"]')).to_have_count(0)
                        page.locator(".admin-nav").get_by_role(
                            "link", name="Radios", exact=True
                        ).click()
                        page.get_by_role("button", name="Désactiver", exact=True).click()
                        expect(page.locator("#admin-stations")).to_contain_text("Collecte arrêtée")
                        page.get_by_role(
                            "button", name="Revenir à la configuration", exact=True
                        ).click()
                        expect(page.locator("#admin-stations")).to_contain_text("Collecte active")
                        page.locator(".admin-nav").get_by_role(
                            "link", name="Catalogue", exact=True
                        ).click()
                        page.locator('#admin-catalog-form [name="review"]').uncheck()
                        expect(page.locator(".admin-track")).to_have_count(5)
                        page.locator(".admin-track").first.click()
                        expect(page.locator("#admin-editor .catalog-editor")).to_be_visible()
                        assert page.evaluate(
                            "!JSON.stringify({...localStorage, ...sessionStorage})"
                            ".includes('browser-test-only')"
                        )
                        page.screenshot(
                            path=f"/tmp/radio-admin-{browser.browser_type.name}-{width}.png"
                        )
                        assert_uninterrupted()
                        cookies = page.context.cookies()
                        admin_cookie = next(
                            cookie for cookie in cookies if cookie["name"] == "__Host-radio_admin"
                        )
                        assert (
                            admin_cookie["httpOnly"]
                            and admin_cookie["secure"]
                            and admin_cookie["sameSite"] == "Strict"
                        )
                        assert "__Host-radio_admin" not in page.evaluate("document.cookie")
                        if width <= 760:
                            page.locator("#mobile-nav-toggle").click()
                        navigation.get_by_role("link", name="Stats", exact=True).click()
                        page.locator("#admin-session-controls a").click()
                        expect(page).to_have_url(base_url + "/admin/dashboard")
                        expect(page.locator("#admin-workspace")).to_be_visible()
                        other = page.context.new_page()
                        other.goto(base_url + "/admin/catalogue")
                        expect(other.locator("#admin-workspace")).to_be_visible()
                        other.close()
                        page.locator("#admin-session-controls [data-admin-logout]").click()
                        expect(page.locator("#admin-login")).to_be_visible()
                        expect(page.locator("#admin-session-controls")).not_to_be_visible()
                        page.locator('[name="token"]').fill("browser-test-only")
                        page.get_by_role("button", name="Ouvrir l’administration").click()
                        expect(page.locator("#admin-workspace")).to_be_visible()
                        assert_uninterrupted()
                        if width <= 760:
                            page.locator("#mobile-nav-toggle").click()
                        navigation.get_by_role("link", name="Recherche avancée").click()
                        expect(page.locator("h1")).to_have_text("Recherche avancée")
                        page.locator("#result-view").select_option("plays")
                        expect(page.locator("#results tbody tr")).to_have_count(7)
                        assert_uninterrupted()
                        if width <= 760:
                            page.locator("#mobile-nav-toggle").click()
                        navigation.get_by_role("link", name="Classements").click()
                        expect(page.locator("h1")).to_have_text("Classements")
                        assert_uninterrupted()
                        page.locator("#chart-list .chart-row").first.click()
                        expect(page.locator(".catalog-dialog")).to_be_visible()
                        expect(page.locator(".catalog-dialog meter")).to_have_count(0)
                        expect(page.locator(".catalog-dialog .catalog-modify")).to_be_visible()
                        assert page.locator(".catalog-summary").evaluate("""summary => {
                            const cover = summary.querySelector('.catalog-art')
                                .getBoundingClientRect();
                            const info = summary.querySelector('.catalog-essentials')
                                .getBoundingClientRect();
                            return cover.width >= (innerWidth > 760 ? 300 : 240)
                                && Math.abs(cover.width - cover.height) < 1
                                && (innerWidth > 760 ? info.left >= cover.right
                                    : info.top >= cover.bottom)
                                && summary.scrollWidth <= summary.clientWidth;
                        }"""), width
                        page.screenshot(path=f"/tmp/radio-identity-light-{width}.png")
                        page.locator(".catalog-dialog .catalog-modify").click()
                        expect(page.locator(".admin-edit-dialog .catalog-editor")).to_be_visible()
                        expect(
                            page.locator('.admin-edit-dialog input[type="password"]')
                        ).to_have_count(0)
                        response = page.locator(".admin-edit-dialog .candidate-response pre").first
                        expect(response).to_contain_text('"provider": "deezer"')
                        layout = page.locator(".admin-edit-dialog .candidate-layout").first
                        assert layout.evaluate("""(row) => {
                            const left = row.querySelector('.candidate-presentation')
                                .getBoundingClientRect();
                            const right = row.querySelector('.candidate-response')
                                .getBoundingClientRect();
                            return innerWidth > 760
                                ? right.left >= left.right : right.top >= left.bottom;
                        }""")
                        layout.scroll_into_view_if_needed()
                        page.screenshot(
                            path=f"/tmp/radio-admin-response-{browser.browser_type.name}-{width}.png"
                        )
                        page.locator(".admin-edit-dialog").get_by_role(
                            "button", name="Choisir cette version", exact=True
                        ).click()
                        expect(response).to_contain_text('"manual": true')
                        expect(page.locator(".admin-edit-dialog")).to_contain_text(
                            "Correction enregistrée"
                        )
                        page.locator(".admin-edit-dialog .catalog-close").click()
                        expect(page.locator(".catalog-dialog")).to_be_visible()
                        page.locator(".catalog-platforms summary").click()
                        expect(page.locator(".catalog-platforms .platform-link")).to_have_count(3)
                        expect(
                            page.locator('.catalog-dialog a[href*="open.spotify.com/search/"]')
                        ).to_be_visible()
                        expect(
                            page.locator(".catalog-platforms").get_by_role(
                                "link", name="Rechercher sur Spotify", exact=True
                            )
                        ).to_be_visible()
                        expect(page.locator(".catalog-dialog .catalog-editor")).to_have_count(0)
                        expect(page.locator(".catalog-radio .catalog-recent li")).to_have_count(1)
                        page.screenshot(
                            path=f"/tmp/radio-track-detail-{browser.browser_type.name}-{width}.png"
                        )
                        expect(page.locator(".catalog-footer button")).to_have_text(
                            "Masquer pour tous"
                        )
                        assert page.locator(".catalog-footer").evaluate("""e => {
                            const button = e.querySelector('button').getBoundingClientRect();
                            const footer = e.getBoundingClientRect();
                            return Math.abs(button.right - footer.right) < 2
                                && e === e.parentElement.lastElementChild;
                        }""")
                        assert_uninterrupted()
                        page.keyboard.press("Escape")
                        expect(page.locator(".catalog-dialog")).not_to_be_visible()
                        page.keyboard.press("Control+k")
                        expect(page.locator(".music-search-dialog")).to_be_visible()
                        page.keyboard.press("Escape")
                        if width <= 760:
                            page.locator("#mobile-nav-toggle").click()
                        page.locator(".search-shortcut").click()
                        expect(page.locator(".music-search-dialog")).to_be_visible()
                        page.locator(".music-search-dialog input").fill("Mosimann Soon")
                        expect(page.locator(".music-search-result")).to_have_count(1)
                        page.screenshot(
                            path=f"/tmp/radio-search-{browser.browser_type.name}-{width}.png"
                        )
                        assert_uninterrupted()
                        page.locator(".music-search-result").click()
                        expect(page.locator(".catalog-dialog")).to_be_visible()
                        expect(page.locator(".music-search-dialog")).not_to_be_visible()
                        assert_uninterrupted()
                        page.get_by_role("button", name="← Retour à la recherche").click()
                        expect(page.locator(".music-search-dialog")).to_be_visible()
                        expect(page.locator(".music-search-dialog input")).to_have_value(
                            "Mosimann Soon"
                        )
                        expect(page.locator(".music-search-result")).to_have_count(1)
                        page.mouse.click(5, 5)
                        expect(page.locator(".music-search-dialog")).not_to_be_visible()
                        if width <= 760:
                            page.locator("#mobile-nav-toggle").click()
                        page.locator(".search-shortcut").click()
                        expect(page.locator(".music-search-dialog input")).to_have_value(
                            "Mosimann Soon"
                        )
                        page.locator(".music-search-result").click()
                        expect(page.locator(".catalog-dialog")).to_be_visible()
                        page.mouse.click(5, 5)
                        expect(page.locator(".catalog-dialog")).not_to_be_visible()
                        assert_uninterrupted()
                        # Click through a track and follow browser history in both directions.
                        page.locator(".podium-detail").first.click()
                        expect(page.locator(".catalog-dialog")).to_be_visible()
                        page.locator(".catalog-search-link").first.click()
                        expect(page.locator("h1")).to_have_text("Recherche avancée")
                        expect(page.locator("#results tbody tr")).to_have_count(3)
                        assert_uninterrupted()
                        page.go_back()
                        expect(page.locator("h1")).to_have_text("Classements")
                        assert_uninterrupted()
                        page.go_forward()
                        expect(page.locator("h1")).to_have_text("Recherche avancée")
                        assert_uninterrupted()
                        page.locator("#player-mute").click()
                        assert page.locator("#radio-player").evaluate("(e) => e.muted")
                        page.locator("#player-mute").click()
                        assert not page.locator("#radio-player").evaluate("(e) => e.muted")
                        page.locator("#player-toggle").click()
                        assert page.locator("#radio-player").evaluate("(e) => e.paused")
                        page.locator("#player-toggle").click()
                        page.wait_for_function(
                            '() => !document.querySelector("#radio-player").paused'
                        )
                        if width <= 760:
                            page.locator("#mobile-nav-toggle").click()
                        navigation.get_by_role("link", name="Classements").click()
                        expect(page.locator("h1")).to_have_text("Classements")
                        page.route("**/api/catalog/*/preview", audio_response)
                        page.locator("#chart-list .preview-button").first.click()
                        expect(page.locator("#chart-list .preview-button").first).to_have_attribute(
                            "aria-pressed", "true"
                        )
                        assert page.evaluate(
                            'document.querySelector("#radio-player") === window.retainedPlayer '
                            "&& !retainedPlayer.paused"
                        )
                        expect(page.locator("audio")).to_have_count(1)
                        expect(page.locator(".player")).to_have_attribute("data-mode", "preview")
                        expect(page.locator("#player-seek")).to_be_visible()
                        expect(page.locator(".player-live")).not_to_be_visible()
                        page.wait_for_function(
                            '() => document.querySelector("#radio-player").duration > 0'
                        )
                        page.locator("#player-progress").evaluate(
                            """e => {
                                e.value=500;
                                e.dispatchEvent(new Event('input',{bubbles:true}));
                            }"""
                        )
                        page.wait_for_function(
                            '() => document.querySelector("#radio-player").currentTime >= 59',
                            timeout=2000,
                        )
                        page.locator("#player-volume").evaluate(
                            """e => {
                                e.value=.25;
                                e.dispatchEvent(new Event('input',{bubbles:true}));
                            }"""
                        )
                        assert (
                            abs(page.locator("#radio-player").evaluate("e => e.volume") - 0.25)
                            < 0.01
                        )
                        page.screenshot(
                            path=f"/tmp/radio-preview-{browser.browser_type.name}-{width}.png"
                        )
                        page.locator("#chart-list .preview-button").first.click()
                        assert page.locator("#radio-player").evaluate("e => e.paused")
                        page.locator("#chart-list .preview-button").first.click()
                        page.wait_for_function(
                            '() => !document.querySelector("#radio-player").paused'
                        )
                        page.locator("#player-return-radio").click()
                        expect(page.locator(".player")).to_have_attribute("data-mode", "radio")
                        expect(page.locator("#player-seek")).not_to_be_visible()
                        page.wait_for_function(
                            '() => !document.querySelector("#radio-player").paused'
                        )
                        assert (
                            abs(page.locator("#radio-player").evaluate("e => e.volume") - 0.25)
                            < 0.01
                        )
                        # Hiding and restoring a title changes ranking and export before pagination.
                        page.locator(".podium-detail").nth(2).click()
                        page.get_by_role("button", name="Masquer pour tous", exact=True).click()
                        expect(page.locator(".podium-detail").nth(2)).not_to_contain_text(
                            "Track Four"
                        )
                        if width <= 760:
                            page.locator("#chart-controls > summary").click()
                        page.locator("[data-open-export]").click()
                        export_dialog = page.locator(".export-dialog")
                        expect(export_dialog).to_be_visible()
                        export_dialog.locator("select[name=format]").select_option("json")
                        with page.expect_download() as download:
                            export_dialog.locator("[type=submit]").click()
                        exported = json.loads(Path(download.value.path()).read_text())
                        assert exported["count"] == 4, exported
                        assert not any(t["title"] == "Track Four" for t in exported["items"])
                        export_dialog.locator("select[name=format]").select_option("csv")
                        with page.expect_download() as download:
                            export_dialog.locator("[type=submit]").click()
                        assert download.value.suggested_filename.endswith(".csv")
                        export_dialog.locator("[data-export-close]").click()
                        page.locator("[data-open-exclusions]").click()
                        expect(page.locator(".exclusions-dialog")).to_be_visible()
                        page.get_by_role("button", name="Réafficher pour tous", exact=True).click()
                        page.mouse.click(5, 5)
                        expect(page.locator(".exclusions-dialog")).not_to_be_visible()
                        expect(page.locator(".podium-detail").nth(2)).to_contain_text("Track Four")
                        # A failed page request must leave the live player running.
                        page.route(
                            "**/recherche",
                            lambda route: route.fulfill(status=503, body="Unavailable"),
                        )
                        if width <= 760:
                            page.locator("#mobile-nav-toggle").click()
                        navigation.get_by_role("link", name="Recherche avancée").click()
                        expect(page.locator("#navigation-status")).to_contain_text("indisponible")
                        assert page.evaluate(
                            'document.querySelector("#radio-player") === window.retainedPlayer '
                            "&& !retainedPlayer.paused"
                        )
                        page.unroute("**/recherche")
                        if width <= 760:
                            page.locator("#mobile-nav-toggle").click()
                        navigation.get_by_role("link", name="Classements").click()
                        expect(page.locator("h1")).to_have_text("Classements")
                        expect(page.locator("#page-content")).not_to_have_attribute(
                            "aria-busy", "true"
                        )
                        expect(page.locator(".podium-card")).to_have_count(3)
                        page.locator(".podium").scroll_into_view_if_needed()
                        page.screenshot(path=f"/tmp/radio-{browser.browser_type.name}-{width}.png")
                        page.locator("#admin-session-controls [data-admin-logout]").click()
                        expect(page.locator("#admin-session-controls")).not_to_be_visible()
                        page.locator(".podium-detail").first.click()
                        expect(page.locator(".catalog-dialog .catalog-modify")).not_to_be_visible()
                        expect(
                            page.locator('.catalog-dialog input[type="password"]')
                        ).to_have_count(0)
                        page.keyboard.press("Escape")
                        # The large artwork has a readable fallback, including missing metadata.
                        page.evaluate(
                            "TrackCatalog.open({artist: 'Mosimann', title: 'Track Four'})"
                        )
                        expect(
                            page.locator(".catalog-summary .catalog-art-placeholder")
                        ).to_be_visible()
                        page.locator(".catalog-toolbar .catalog-close").click()

                        def incomplete_catalog(route):
                            data = route.fetch().json()
                            data.update(
                                cover_url=None,
                                preview_url=None,
                                artist=None,
                                title="Un titre très long " * 12,
                            )
                            route.fulfill(json=data)

                        page.route("**/api/catalog?**", incomplete_catalog)
                        page.evaluate("TrackCatalog.open({artist: '', title: 'Track Five'})")
                        expect(
                            page.locator(".catalog-summary .catalog-art-placeholder")
                        ).to_be_visible()
                        expect(page.locator(".catalog-summary .preview-button")).to_be_disabled()
                        expect(page.locator(".catalog-summary .preview-entry")).to_contain_text(
                            "Aucun extrait disponible"
                        )
                        expect(page.locator(".catalog-artist")).to_have_text("Artiste inconnu")
                        assert page.locator(".catalog-dialog").evaluate(
                            "e => e.scrollWidth <= e.clientWidth"
                        )
                        page.keyboard.press("Escape")
                        page.unroute("**/api/catalog?**", incomplete_catalog)
                        # Playback remains synchronized between the sheet and the persistent player.
                        page.locator("#theme-toggle").click()
                        page.locator(".podium-detail").first.click()
                        sheet_play = page.locator(".catalog-summary .preview-button")
                        sheet_play.click()
                        expect(sheet_play).to_have_attribute("aria-pressed", "true")
                        expect(page.locator(".player")).to_have_attribute("data-mode", "preview")
                        sheet_play.click()
                        expect(sheet_play).to_have_attribute("aria-pressed", "false")
                        page.locator(".catalog-technical summary").click()
                        expect(page.locator(".catalog-technical dl")).to_be_visible()
                        page.locator(".catalog-dialog").evaluate("e => e.scrollTop = 0")
                        page.screenshot(path=f"/tmp/radio-identity-dark-{width}.png")
                        page.keyboard.press("Escape")
                        assert not errors, errors
                        print(
                            {
                                "browser": browser.browser_type.name,
                                "viewport": f"{width}x{height}",
                                "ranks_and_square_covers": "passed",
                                "uninterrupted_navigation": "passed",
                                "controls_and_failure": "passed",
                            },
                            flush=True,
                        )
                        page.close()
                        context.close()
            finally:
                for browser in browsers:
                    browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
        log.close()
