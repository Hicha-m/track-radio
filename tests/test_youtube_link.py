import asyncio

import pytest
from fastapi import HTTPException

from app.models import ServiceQuota, YouTubeMatch
from app.services.catalog import ensure_entry
from app.services.youtube import verify_link, video_id_from_url

AUTH = {"Authorization": "Bearer test-admin-token"}


def test_only_explicit_youtube_https_links_are_accepted():
    assert video_id_from_url("https://youtu.be/aaaaaaaaaaa?t=10") == "aaaaaaaaaaa"
    assert (
        video_id_from_url("https://www.youtube.com/watch?v=aaaaaaaaaaa&list=ignored")
        == "aaaaaaaaaaa"
    )
    for url in [
        "https://example.test/aaaaaaaaaaa",
        "http://youtu.be/aaaaaaaaaaa",
        "https://youtube.com@127.0.0.1/watch?v=aaaaaaaaaaa",
        "https://youtu.be:444/aaaaaaaaaaa",
        "https://youtu.be/short",
        "https://www.youtube.com/redirect?q=https://example.test",
    ]:
        with pytest.raises(ValueError):
            video_id_from_url(url)


def test_oembed_checks_artist_title_and_version_without_search_api(monkeypatch):
    from app.services import catalog

    calls = []

    async def fetch(client, url, params):
        calls.append((url, params))
        return {"type": "video", "title": "Artist Song (Official Audio)", "author_name": "Artist"}

    monkeypatch.setattr(catalog, "fetch_json", fetch)
    found = asyncio.run(verify_link("Artist", "Song", "https://youtu.be/aaaaaaaaaaa"))
    assert found["id"] == "aaaaaaaaaaa"
    assert calls[0][0] == "https://www.youtube.com/oembed"
    assert "key" not in calls[0][1]
    with pytest.raises(HTTPException) as error:
        asyncio.run(verify_link("Other Artist", "Song", "https://youtu.be/aaaaaaaaaaa"))
    assert error.value.status_code == 422
    with pytest.raises(HTTPException):
        asyncio.run(verify_link("Artist", "Song (Live)", "https://youtu.be/aaaaaaaaaaa"))


def test_verified_link_is_admin_only_revision_safe_and_uses_no_search_budget(
    db, client, monkeypatch
):
    entry = ensure_entry(db, "Artist", "Song")
    db.commit()

    async def verified(*args):
        return {"id": "aaaaaaaaaaa", "title": "Artist Song", "channel": "Artist", "score": 5}

    monkeypatch.setattr("app.services.youtube.verify_link", verified)
    path = f"/api/catalog/{entry.id}/youtube/link"
    body = {"url": "https://youtu.be/aaaaaaaaaaa", "revision": 0}
    assert client.post(path, json=body).status_code == 401
    response = client.post(path, json=body, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["manual"] and response.json()["selected"]["id"] == "aaaaaaaaaaa"
    assert db.get(ServiceQuota, "youtube") is None
    assert client.post(path, json=body, headers=AUTH).status_code == 409
    assert db.get(YouTubeMatch, entry.id).revision == 1
