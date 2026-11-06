"""An automatic preview must not bypass either exclusion list, including as admin."""

import json

import pytest
from fastapi.responses import RedirectResponse

from app.models import GlobalExclusion
from app.services.catalog import ensure_entry
from app.services.exclusions import exclusion_id, normalized_pair


def blacklist(db, title):
    artist, normalized_title = normalized_pair("Artist", title)
    db.add(
        GlobalExclusion(
            id=exclusion_id(artist, normalized_title),
            artist=artist,
            title=normalized_title,
            artist_normalized=artist,
            title_normalized=normalized_title,
        )
    )
    db.commit()


@pytest.mark.parametrize("admin", [False, True])
def test_preview_enforces_personal_and_global_hiding_before_provider_io(
    db, client, monkeypatch, admin
):
    entries = [ensure_entry(db, "Artist", title) for title in ("Visible", "Personal", "Global")]
    for entry in entries:
        entry.selected = {"provider": "itunes", "provider_id": entry.title}
    db.commit()
    blacklist(db, "Global")
    calls = []

    async def provider(selected):
        calls.append(selected["provider_id"])
        return RedirectResponse("https://example.test/preview.wav")

    monkeypatch.setattr("app.catalog_api.recording_preview", provider)
    headers = {"Authorization": "Bearer test-admin-token"} if admin else {}
    params = {"exclusions": json.dumps([{"artist": "ARTIST", "title": "PERSONAL"}])}
    for entry, expected in zip(entries, (307, 404, 404), strict=True):
        result = client.get(
            f"/api/catalog/{entry.id}/preview",
            params=params,
            headers=headers,
            follow_redirects=False,
        )
        assert result.status_code == expected
    assert calls == ["Visible"]
    assert (
        client.get(
            f"/api/catalog/{entries[0].id}/preview", params={"exclusions": "[broken"}
        ).status_code
        == 422
    )


def test_preview_rechecks_global_visibility_after_refreshing_the_provider_url(
    db, client, monkeypatch
):
    entry = ensure_entry(db, "Artist", "Becomes hidden")
    entry.selected = {"provider": "itunes", "provider_id": "after"}
    db.commit()

    async def provider(_selected):
        blacklist(db, "Becomes hidden")
        return RedirectResponse("https://example.test/preview.wav")

    monkeypatch.setattr("app.catalog_api.recording_preview", provider)
    result = client.get(
        f"/api/catalog/{entry.id}/preview",
        headers={"Authorization": "Bearer test-admin-token"},
        follow_redirects=False,
    )
    assert result.status_code == 404
    assert "location" not in result.headers
