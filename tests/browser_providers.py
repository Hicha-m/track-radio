"""Phase 4 manual provider journeys against a temporary database and simulated APIs."""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

RECORDING = "01234567-1234-1234-1234-012345678901"
with tempfile.TemporaryDirectory(prefix="radio-providers-") as directory:
    os.environ["DATABASE_URL"] = f"sqlite:///{directory}/test.db"
    os.environ["ADMIN_TOKEN"] = "browser-test-only"
    os.environ["LASTFM_API_KEY"] = "server-test-key"
    from app.database import SessionLocal, engine
    from app.models import Base, TrackMetadata
    from app.services.catalog import ensure_entry

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        entry = ensure_entry(db, "GRADUR", "Comme un sheguey (feat. L2b)")
        key = entry.id
        entry.selected = {
            "provider": "itunes",
            "provider_id": "1",
            "title": entry.title,
            "artist": "GRADUR",
            "genres": [],
        }
        entry.status = "matched"
        db.commit()
    Path(directory, "provider_app.py").write_text("""from app.main import app as application
from app.services import providers
import httpx
async def simulated(client, url, params=None, **kwargs):
    if 'musicbrainz.org' in url:
        return {'count': 1, 'recordings': [{'id':'01234567-1234-1234-1234-012345678901',
            'title':'Comme un sheguey', 'artist-credit':[{'name':'GRADUR'}],
            'releases':[{'id':'01234567-1234-1234-1234-012345678902','title':'Album'}]}]}
    if 'coverartarchive.org' in url:
        return {'images':[{'front':True,'thumbnails':{'500':'http://coverartarchive.org/cover.svg'}}]}
    if 'audioscrobbler.com' in url:
        assert params['api_key'] == 'server-test-key'
        assert params['artist'] == 'GRADUR' and params['track'] == 'Comme un sheguey'
        return {'results':{'opensearch:totalResults':'31','trackmatches':{'track':[
            {'name': 'Match page ' + str(params['page']), 'artist':'GRADUR',
             'url':'https://www.last.fm/music/Gradur/_/Song'}]}}}
    if '/suggest/' in url:
        return {'data':[{'id':42,'title':'Comme un sheguey','artist':{'name':'GRADUR'}},
                        {'id':43,'title':'Missing','artist':{'name':'GRADUR'}}]}
    if '/v1/' in url:
        if url.endswith('/Missing'):
            response = httpx.Response(404, request=httpx.Request('GET', url))
            raise httpx.HTTPStatusError('not found', request=response.request, response=response)
        return {'lyrics':'Actual supplied lyrics'}
    raise AssertionError(url)
providers.fetch_json = simulated
""")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with open(f"{directory}/server.log", "w+") as log:
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "provider_app:application",
                "--app-dir",
                directory,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
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
            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    for width in (1280, 402):
                        context = browser.new_context(viewport={"width": width, "height": 900})
                        page = context.new_page()
                        errors = []
                        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                        page.route(
                            "https://coverartarchive.org/**",
                            lambda route: route.fulfill(
                                content_type="image/svg+xml",
                                body=(
                                    '<svg xmlns="http://www.w3.org/2000/svg" '
                                    'width="100" height="100">'
                                    '<rect width="100" height="100" fill="red"/></svg>'
                                ),
                            ),
                        )
                        page.goto(f"http://127.0.0.1:{port}/admin")
                        page.locator("#admin-login input").fill("browser-test-only")
                        page.locator("#admin-login button").click()
                        expect(page.locator("#admin-workspace")).to_be_visible()
                        page.evaluate("(key) => window.RadioAdmin.openEditor({track_id:key})", key)
                        panel = page.locator(".admin-edit-dialog")
                        expect(panel).to_be_visible()
                        provider = panel.locator("form select").first
                        search = panel.get_by_role(
                            "button", name="Rechercher des propositions", exact=True
                        )
                        provider.select_option("musicbrainz")
                        search.click()
                        expect(panel.locator(".candidate")).to_have_count(1)
                        expect(panel.locator(".candidate .preview-button")).to_have_count(0)
                        panel.get_by_role("button", name="Charger la pochette", exact=True).click()
                        expect(panel.locator(".candidate img")).to_have_attribute(
                            "src", "https://coverartarchive.org/cover.svg"
                        )
                        panel.get_by_role(
                            "button", name="Choisir cette version", exact=True
                        ).click()
                        expect(panel.locator('[role="status"]').last).to_contain_text(
                            "Correction enregistrée"
                        )
                        with SessionLocal() as db:
                            assert db.get(TrackMetadata, key).selected["provider"] == "musicbrainz"
                        provider.select_option("lastfm")
                        search.click()
                        expect(panel.locator(".candidate")).to_contain_text("Match page 1")
                        pager = panel.get_by_role("navigation", name="Pages des propositions")
                        expect(pager).to_contain_text("Page 1")
                        pager.get_by_role("button", name="Suivant").click()
                        expect(panel.locator(".candidate")).to_contain_text("Match page 2")
                        expect(pager).to_contain_text("Page 2")
                        pager.get_by_role("button", name="Précédent").click()
                        expect(panel.locator(".candidate")).to_contain_text("Match page 1")
                        pager.get_by_role("button", name="Suivant").click()
                        expect(panel.locator(".candidate")).to_contain_text("Match page 2")
                        panel.get_by_role(
                            "button", name="Choisir cette version", exact=True
                        ).click()
                        expect(panel.locator('[role="status"]').last).to_contain_text(
                            "Correction enregistrée"
                        )
                        provider.select_option("lyricsovh")
                        search.click()
                        expect(panel.locator(".candidate")).to_have_count(2)
                        rows = panel.locator(".candidate")
                        expect(
                            rows.first.get_by_role("button", name="Choisir cette version")
                        ).to_be_disabled()
                        rows.nth(1).get_by_role("button", name="Charger les paroles").click()
                        expect(rows.nth(1)).to_contain_text("Paroles non disponibles")
                        expect(
                            rows.nth(1).get_by_role("button", name="Choisir cette version")
                        ).to_be_disabled()
                        rows.first.get_by_role("button", name="Charger les paroles").click()
                        expect(rows.first).to_contain_text("Actual supplied lyrics")
                        rows.first.get_by_role("button", name="Choisir cette version").click()
                        expect(panel.locator('[role="status"]').last).to_contain_text(
                            "Correction enregistrée"
                        )
                        with SessionLocal() as db:
                            saved = db.get(TrackMetadata, key)
                            assert saved.selected["provider"] == "lastfm"
                            assert saved.lyrics["provider"] == "lyricsovh"
                        panel.get_by_role("button", name="Fermer", exact=True).click()
                        page.evaluate(
                            '() => window.TrackCatalog.open({artist:"GRADUR",'
                            'title:"Comme un sheguey (feat. L2b)"})'
                        )
                        catalog = page.locator(".catalog-dialog")
                        catalog.locator(".catalog-lyrics summary").click()
                        expect(
                            catalog.get_by_role("link", name="Source des paroles : lyrics.ovh")
                        ).to_be_visible()
                        assert "server-test-key" not in page.content()
                        assert not errors, errors
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                        context.close()
                        print(f"{width}px: new provider journeys: OK")
                finally:
                    browser.close()
        except Exception:
            log.seek(0)
            print(log.read()[-5000:])
            raise
        finally:
            server.terminate()
            server.wait(timeout=10)
