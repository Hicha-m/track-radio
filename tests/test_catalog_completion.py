import asyncio
from copy import deepcopy

import httpx
import pytest

from app.services import catalog
from app.services import catalog_completion as completion


def main_track():
    return {
        "provider": "deezer",
        "provider_id": "1",
        "artist": "Artist",
        "title": "Song",
        "duration": 180,
        "album": "Original Album",
        "genres": [],
        "genre_source": "deezer",
        "isrc": "FR123",
        "preview_url": "https://example.com/preview.mp3",
        "url": "https://www.deezer.com/track/1",
    }


def second_track():
    return {
        "provider": "itunes",
        "provider_id": "42",
        "artist": "Artist",
        "title": "Song",
        "duration": 181,
        "album": "Other Album",
        "genres": ["Rap"],
        "genre_source": "itunes",
        "release_date": "2026-01-01",
        "url": "https://music.apple.com/track/42",
    }


def tayc_tracks():
    selected = {
        **main_track(),
        "provider_id": "3957340471",
        "artist": "Tayc",
        "title": "MAMAN PRIE",
        "album": "JOŸA",
        "duration": 222,
        "isrc": "FR8FB2605750",
    }
    candidate = {
        **second_track(),
        "provider_id": "1893043822",
        "artist": "Tayc & RnBoi",
        "title": "MAMAN PRIE",
        "album": "JOŸA",
        "duration": 222,
        "genres": ["Hip-hop/Rap"],
        "release_date": "2026-05-14T12:00:00Z",
    }
    return selected, candidate


def test_admin_completes_tayc_from_itunes_co_credit(db, client, monkeypatch):
    selected, candidate = tayc_tracks()
    entry = catalog.ensure_entry(db, "Tayc", "MAMAN PRIE")
    entry.selected, entry.status, entry.manual = selected, "matched", True
    db.commit()

    async def search(*args):
        return [candidate]

    monkeypatch.setattr(completion, "search_provider", search)
    response = client.post(
        f"/api/catalog/{entry.id}/complete",
        json={},
        headers={"Authorization": "Bearer test-admin-token"},
    )
    assert response.status_code == 200, response.text
    db.refresh(entry)
    assert entry.manual and entry.primary_genre == "Hip-hop/Rap"
    assert entry.selected["release_date"] == candidate["release_date"]
    assert entry.selected["field_sources"]["genres"] == "itunes"
    for field, value in selected.items():
        if field not in {"genres", "genre_source"}:
            assert entry.selected[field] == value
    # Secondary completion must not relax automatic primary identification.
    assert not catalog.exact_match("Tayc", "MAMAN PRIE", candidate)


@pytest.mark.parametrize(
    "change",
    [
        {"artist": "Tayc Junior & RnBoi"},
        {"artist": "RnBoi & Tayc"},
        {"album": "Another edition"},
        {"album": None},
        {"duration": None},
        {"duration": 230},
        {"title": "MAMAN PRIE (Live)"},
        {"title": "MAMAN PRIE (feat Other)"},
        {"isrc": "OTHER"},
    ],
)
def test_co_credit_requires_matching_edition_and_lead_artist(change):
    selected, candidate = tayc_tracks()
    assert not completion.compatible(selected, {**candidate, **change})


def test_co_credit_with_multiple_matches_remains_ambiguous():
    selected, candidate = tayc_tracks()
    result, attempt = asyncio.run(
        completion.complete_missing(
            None, selected, known_candidates=[candidate, {**candidate, "provider_id": "other"}]
        )
    )
    assert result == selected and attempt["status"] == "ambiguous"


def test_completes_missing_fields_preserving_primary_identity_and_provenance(monkeypatch):
    before = main_track()

    async def search(*args):
        return [second_track()]

    monkeypatch.setattr(completion, "search_provider", search)
    selected, attempt = asyncio.run(completion.complete_missing(None, before))
    assert before["genres"] == []
    assert selected["genres"] == ["Rap"] and selected["genre_source"] == "itunes"
    assert not selected.get("release_date")
    assert selected["album"] == "Original Album"
    for field in [
        "provider",
        "provider_id",
        "artist",
        "title",
        "duration",
        "isrc",
        "preview_url",
        "url",
    ]:
        assert selected[field] == before[field]
    assert selected["field_sources"]["genres"] == "itunes"
    assert selected["field_sources"]["album"] == "deezer"
    assert selected["additional_sources"][0]["provider_id"] == "42"
    assert attempt["filled"] == ["genres"]


def test_rejects_wrong_duration_isrc_artist_guest_and_ambiguous_editions(monkeypatch):
    for candidate in [
        {**second_track(), "duration": 240},
        {**second_track(), "isrc": "OTHER"},
        {**second_track(), "artist": "Other"},
        {**second_track(), "title": "Song (feat Other)"},
    ]:

        async def search(*args, candidate=candidate):
            return [candidate]

        monkeypatch.setattr(completion, "search_provider", search)
        result, attempt = asyncio.run(completion.complete_missing(None, main_track()))
        assert result == main_track() and attempt["status"] == "not_found"

    async def search(*args):
        return [second_track(), {**second_track(), "provider_id": "43"}]

    monkeypatch.setattr(completion, "search_provider", search)
    result, attempt = asyncio.run(completion.complete_missing(None, main_track()))
    assert result == main_track() and attempt["status"] == "ambiguous"


def test_final_absence_not_retried_and_primary_candidates_are_reused(monkeypatch):
    async def forbidden(*args):
        raise AssertionError("Unexpected duplicate provider call")

    monkeypatch.setattr(completion, "search_provider", forbidden)
    _, attempt = asyncio.run(
        completion.complete_missing(None, main_track(), {"status": "not_found"})
    )
    assert attempt["status"] == "not_found"
    result, attempt = asyncio.run(
        completion.complete_missing(None, main_track(), known_candidates=[second_track()])
    )
    assert result["genres"] == ["Rap"] and attempt["status"] == "matched"


def test_automatic_missing_genres_use_secondary_and_stop_on_definitive_answer(monkeypatch):
    calls = []

    async def search(*args):
        calls.append("itunes")
        return [second_track()]

    monkeypatch.setattr(completion, "search_provider", search)
    previous = {
        "artist": "Artist",
        "title": "Song",
        "selected": main_track(),
        "status": "matched",
        "lyrics": {"plain": "preserved"},
        "lyrics_status": "matched",
        "lyrics_candidates": [],
        "candidates": [],
        "attempts": {},
    }
    result = asyncio.run(catalog.enrich_progress(None, previous))
    assert result["selected"]["genres"] == ["Rap"] and result["automatic_done"]
    assert result["lyrics"]["plain"] == "preserved"
    assert result["attempts"]["complement"]["status"] == "matched"
    again = asyncio.run(catalog.enrich_progress(None, result))
    assert calls == ["itunes"] and again["automatic_done"]


def test_temporary_complement_error_keeps_primary_and_is_retryable(monkeypatch):
    async def fail(*args):
        raise httpx.ReadTimeout("temporary")

    monkeypatch.setattr(completion, "search_provider", fail)
    previous = {
        "artist": "Artist",
        "title": "Song",
        "selected": main_track(),
        "status": "matched",
        "lyrics": None,
        "lyrics_status": "not_found",
        "lyrics_candidates": [],
        "candidates": [],
        "attempts": {},
    }
    result = asyncio.run(catalog.enrich_progress(None, previous))
    assert result["status"] == "matched" and not result["automatic_done"]
    assert result["selected"] == main_track()
    assert result["attempts"]["complement"]["status"] == "error"


def test_admin_completion_preserves_manual_choice_and_requires_auth(db, client, monkeypatch):
    entry = catalog.ensure_entry(db, "Artist", "Song")
    entry.selected = main_track()
    entry.status = "matched"
    entry.manual = True
    entry.lyrics = {"plain": "keep"}
    db.commit()

    async def search(*args):
        return [second_track()]

    monkeypatch.setattr(completion, "search_provider", search)
    path = f"/api/catalog/{entry.id}/complete"
    assert client.post(path, json={}).status_code == 401
    result = client.post(path, json={}, headers={"Authorization": "Bearer test-admin-token"})
    assert result.status_code == 200, result.text
    db.refresh(entry)
    assert entry.manual and entry.selected["provider_id"] == "1"
    assert entry.primary_genre == "Rap" and entry.lyrics["plain"] == "keep"


def test_admin_completion_cannot_overwrite_concurrent_correction(db, client, monkeypatch):
    entry = catalog.ensure_entry(db, "Artist", "Song")
    entry.selected = main_track()
    db.commit()

    async def complete(client, selected):
        entry.revision += 1
        entry.selected = {**main_track(), "genres": ["Manually chosen"]}
        db.commit()
        return {**deepcopy(selected), "genres": ["Rap"]}, {"status": "matched"}

    monkeypatch.setattr(completion, "complete_missing", complete)
    response = client.post(
        f"/api/catalog/{entry.id}/complete",
        json={},
        headers={"Authorization": "Bearer test-admin-token"},
    )
    assert response.status_code == 409
    db.refresh(entry)
    assert entry.selected["genres"] == ["Manually chosen"]


def test_album_and_date_are_completed_together_or_from_matching_editions(monkeypatch):
    async def search(*args):
        return [second_track()]

    monkeypatch.setattr(completion, "search_provider", search)
    for album in (None, "Other Album"):
        selected, attempt = asyncio.run(
            completion.complete_missing(None, {**main_track(), "album": album})
        )
        assert selected["album"] == "Other Album"
        assert selected["release_date"] == "2026-01-01"
        assert selected["field_sources"]["release_date"] == "itunes"
