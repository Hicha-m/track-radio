import pytest

from app.services.catalog import ensure_entry
from app.services.catalog_completion import complete_from_candidate

AUTH = {"Authorization": "Bearer test-admin-token"}


@pytest.fixture
def entry(db):
    track = ensure_entry(db, "Quavo", "Backwards (feat. T.I.)")
    track.status = "matched"
    track.selected = {
        "provider": "deezer",
        "provider_id": "42",
        "artist": "Quavo",
        "title": "Backwards (feat. T.I.)",
        "album": "Known Album",
        "duration": None,
        "genres": [],
        "isrc": None,
    }
    track.candidates = [
        {
            "provider": "itunes",
            "provider_id": "192",
            "artist": "Quavo & T.I.",
            "title": "Backwards",
            "album": "Backwards - Single",
            "duration": 192,
            "genres": ["Hip-hop"],
            "genre_source": "itunes",
            "isrc": None,
            "cover_url": "https://images.example.test/backwards.jpg",
            "preview_url": "https://audio.example.test/backwards.m4a",
            "url": "https://music.apple.com/track/192",
        }
    ]
    track.lyrics = {"plain": "Existing lyrics"}
    track.lyrics_status = "matched"
    db.commit()
    return track


def test_candidate_completion_preserves_values_and_lyrics_and_exposes_only_useful_actions(
    db, client, entry
):
    path = f"/api/catalog/{entry.id}/complete-candidate"
    payload = {"candidate_id": "itunes:192", "revision": entry.revision}
    assert client.post(path, json=payload).status_code == 401
    admin = client.get(f"/api/admin/catalog/{entry.id}", headers=AUTH).json()
    assert "genres" in admin["candidate_completions"]["itunes:192"]
    response = client.post(path, json=payload, headers=AUTH)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["provider"] == "deezer" and data["provider_id"] == "42"
    assert data["title"] == "Backwards (feat. T.I.)" and data["artist"] == "Quavo"
    assert data["album"] == "Known Album" and data["duration"] == 192
    assert data["genres"] == ["Hip-hop"] and not data["isrc"]
    assert data["field_sources"]["preview_url"] == "itunes"
    assert data["additional_sources"][0]["url"] == "https://music.apple.com/track/192"
    assert data["candidate_completions"]["itunes:192"] == []
    db.refresh(entry)
    assert entry.primary_genre == "Hip-hop" and entry.manual
    assert entry.lyrics == {"plain": "Existing lyrics"} and entry.lyrics_status == "matched"
    # A repeated no-op neither invents data nor changes the revision.
    payload["revision"] = entry.revision
    again = client.post(path, json=payload, headers=AUTH).json()
    assert again["completion"]["filled"] == [] and again["revision"] == payload["revision"]
    assert client.get("/api/catalog", params={"artist": "Quavo", "title": entry.title}).json()[
        "genres"
    ] == ["Hip-hop"]


def test_candidate_completion_rejects_stale_unknown_or_forged_results(db, client, entry):
    path = f"/api/catalog/{entry.id}/complete-candidate"
    payload = {"candidate_id": "itunes:192", "revision": entry.revision}
    assert (
        client.post(
            path, json={**payload, "revision": entry.revision + 1}, headers=AUTH
        ).status_code
        == 409
    )
    assert (
        client.post(
            path, json={**payload, "candidate_id": "itunes:missing"}, headers=AUTH
        ).status_code
        == 422
    )
    assert (
        client.post(path, json={**payload, "genres": ["Invented"]}, headers=AUTH).status_code == 422
    )
    entry.selected = None
    db.commit()
    assert client.post(path, json=payload, headers=AUTH).status_code == 409


def test_missing_candidate_data_never_removes_existing_metadata(entry):
    before = {
        **entry.selected,
        "genres": ["Rap"],
        "duration": 190,
        "isrc": "KEEP",
        "cover_url": "https://images.example.test/existing.jpg",
        "preview_url": "https://audio.example.test/existing.mp3",
        "additional_sources": [
            {
                "provider": "itunes",
                "provider_id": "other",
                "url": "https://music.apple.com/existing",
            }
        ],
    }
    result, fields = complete_from_candidate(before, entry.candidates[0])
    assert not fields and result == before
    empty = {"provider": "itunes", "provider_id": "empty", "artist": "Quavo", "title": "Backwards"}
    result, fields = complete_from_candidate(entry.selected, empty)
    assert not fields and result == entry.selected


def test_candidate_preview_refreshes_without_selecting_and_preserves_public_preview(
    db, client, entry, monkeypatch
):
    from app.catalog_api import _preview_cache

    _preview_cache.clear()
    candidate = {**entry.candidates[0], "provider": "deezer", "provider_id": "191"}
    entry.candidates = [candidate]
    db.commit()
    calls = []

    async def fresh(*args):
        calls.append(args[1])
        return {"preview": "https://audio.example.test/fresh.mp3"}

    monkeypatch.setattr("app.catalog_api.fetch_json", fresh)
    path = f"/api/catalog/{entry.id}/candidates/deezer:191/preview"
    assert client.get(path, follow_redirects=False).status_code == 401
    response = client.get(path, headers=AUTH, follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "https://audio.example.test/fresh.mp3"
    assert response.headers["cache-control"] == "no-store"
    assert calls == ["https://api.deezer.com/track/191"]
    db.refresh(entry)
    assert entry.selected["provider_id"] == "42"
    assert client.get(f"/api/catalog/{entry.id}/preview", follow_redirects=False).status_code == 307
    assert calls[-1] == "https://api.deezer.com/track/42"
    assert client.get(path.replace("191", "missing"), headers=AUTH).status_code == 404


def test_complementary_preview_uses_its_actual_source(db, client, entry, monkeypatch):
    from app.catalog_api import _preview_cache

    _preview_cache.clear()
    completed, _ = complete_from_candidate(entry.selected, entry.candidates[0])
    entry.selected = completed
    db.commit()

    async def forbidden(*args):
        pytest.fail("The excerpt comes from iTunes, not Deezer")

    monkeypatch.setattr("app.catalog_api.fetch_json", forbidden)
    response = client.get(f"/api/catalog/{entry.id}/preview", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == entry.candidates[0]["preview_url"]


def test_candidate_actions_require_csrf_with_cookie_auth(client, entry):
    client.base_url = "https://testserver"
    response = client.post(
        "/api/admin/login",
        json={"token": "test-admin-token"},
        headers={
            "Origin": "https://testserver",
            "X-Admin-Login": "1",
        },
    )
    assert response.status_code == 200
    path = f"/api/catalog/{entry.id}/complete-candidate"
    payload = {"candidate_id": "itunes:192", "revision": entry.revision}
    assert client.post(path, json=payload).status_code == 403
    assert (
        client.post(
            path,
            json=payload,
            headers={
                "Origin": "https://testserver",
                "X-CSRF-Token": response.json()["csrf_token"],
            },
        ).status_code
        == 200
    )


def test_completion_preview_keeps_the_exact_candidate_id(db, client, entry, monkeypatch):
    from app.catalog_api import _preview_cache

    _preview_cache.clear()
    candidate = {**entry.candidates[0], "provider": "deezer", "provider_id": "191"}
    entry.selected, _ = complete_from_candidate(entry.selected, candidate)
    db.commit()

    async def fresh(*args):
        assert args[1] == "https://api.deezer.com/track/191"
        return {"preview": "https://audio.example.test/exact-candidate.mp3"}

    monkeypatch.setattr("app.catalog_api.fetch_json", fresh)
    response = client.get(f"/api/catalog/{entry.id}/preview", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].endswith("exact-candidate.mp3")
    assert entry.selected["provider_id"] == "42"
