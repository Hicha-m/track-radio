from datetime import UTC, datetime, timedelta

from app.models import Play, Station
from app.services.data_admin import utc
from app.services.track_identity import identity

AUTH = {"Authorization": "Bearer test-admin-token"}


def seed(db):
    radio = Station(name="Mouv'", slug="mouv", stream_url="https://example.test/live")
    other = Station(name="Autre", slug="other", stream_url="https://example.test/other")
    db.add_all([radio, other])
    db.flush()
    now = datetime.now(UTC) - timedelta(hours=1)
    rows = []
    for station, title, seconds in [
        (radio, "Song", 0),
        (radio, "Other", 20),
        (radio, "Song", 42),
        (radio, "Song", 300),
        (other, "Song", 60),
    ]:
        row = Play(
            station_id=station.id,
            title=title,
            artist="Artist",
            source="json",
            raw_metadata="Original source",
            detected_at=now + timedelta(seconds=seconds),
            last_seen_at=now + timedelta(seconds=seconds),
        )
        db.add(row)
        rows.append(row)
    db.commit()
    return radio, rows


def test_broadcast_search_proximity_sort_and_auth(db, client):
    radio, rows = seed(db)
    assert client.get("/api/admin/plays").status_code == 401
    result = client.get(
        "/api/admin/plays",
        headers=AUTH,
        params={
            "suspicious": True,
            "station_id": radio.id,
            "q": "song",
        },
    )
    assert result.status_code == 200, result.text
    assert [r["id"] for r in result.json()["items"]] == [rows[2].id]
    assert result.json()["items"][0]["previous_at"] is not None
    result = client.get("/api/admin/plays?sort=oldest", headers=AUTH).json()
    assert result["items"][0]["id"] == rows[0].id
    for params in [{"sort": "injection"}, {"since": "2026-01-01T00:00:00"}, {"page": 0}]:
        assert client.get("/api/admin/plays", params=params, headers=AUTH).status_code == 422


def preview(client, command):
    result = client.post("/api/admin/plays/preview", json=command, headers=AUTH)
    assert result.status_code == 200, result.text
    return result.json()


def test_preview_merge_keeps_chosen_source_and_real_replay(db, client):
    _, rows = seed(db)
    first, duplicate, replay = rows[0], rows[2], rows[3]
    plan = preview(
        client, {"action": "merge", "ids": [first.id, duplicate.id], "keep_id": first.id}
    )
    assert db.query(Play).count() == 5
    assert len(plan["before"]) == 2 and len(plan["after"]) == 1
    assert client.post("/api/admin/plays/apply", json={"token": plan["token"]}).status_code == 401
    result = client.post("/api/admin/plays/apply", json={"token": plan["token"]}, headers=AUTH)
    assert result.status_code == 200, result.text
    assert db.get(Play, duplicate.id) is None
    assert db.get(Play, replay.id) is not None
    assert first.raw_metadata == "Original source"
    assert utc(first.last_seen_at) == utc(duplicate.last_seen_at)
    assert (
        client.post(
            "/api/admin/plays/apply", json={"token": plan["token"]}, headers=AUTH
        ).status_code
        == 409
    )


def test_preview_corrects_identity_without_rewriting_source(db, client):
    _, rows = seed(db)
    row = rows[0]
    plan = preview(
        client, {"action": "correct", "ids": [row.id], "artist": "New Artist", "title": "New Title"}
    )
    assert row.title == "Song"
    response = client.post("/api/admin/plays/apply", json={"token": plan["token"]}, headers=AUTH)
    assert response.status_code == 200, response.text
    assert row.title == "New Title" and row.track_id == identity("New Artist", "New Title")
    assert row.raw_metadata == "Original source"
    assert client.get("/api/admin/catalog?review=false&q=New", headers=AUTH).json()["items"]


def test_preview_rejects_changed_data_tampering_and_invalid_merges(db, client):
    _, rows = seed(db)
    plan = preview(client, {"action": "delete", "ids": [rows[0].id]})
    rows[0].last_seen_at += timedelta(seconds=1)
    db.commit()
    assert (
        client.post(
            "/api/admin/plays/apply", json={"token": plan["token"]}, headers=AUTH
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/admin/plays/apply", json={"token": plan["token"] + "x"}, headers=AUTH
        ).status_code
        == 409
    )
    for ids in [[rows[0].id, rows[1].id], [rows[0].id, rows[4].id]]:
        assert (
            client.post(
                "/api/admin/plays/preview",
                json={"action": "merge", "ids": ids, "keep_id": ids[0]},
                headers=AUTH,
            ).status_code
            == 422
        )
    plan = preview(client, {"action": "delete", "ids": [rows[0].id]})
    assert (
        client.post(
            "/api/admin/plays/apply", json={"token": plan["token"]}, headers=AUTH
        ).status_code
        == 200
    )
    assert db.get(Play, rows[0].id) is None


def test_expired_preview_and_date_correction_refresh_cached_statistics(db, client, monkeypatch):
    from app.services.stats import cached_top

    radio, rows = seed(db)
    before = cached_top(db, radio.id, None, None, 25)
    assert next(count for _, title, count in before if title == "Song") == 3
    plan = preview(client, {"action": "delete", "ids": [rows[0].id]})
    import app.services.data_admin as service

    clock = service.time.time
    monkeypatch.setattr(service.time, "time", lambda: clock() + 601)
    assert (
        client.post(
            "/api/admin/plays/apply", json={"token": plan["token"]}, headers=AUTH
        ).status_code
        == 409
    )
    monkeypatch.undo()
    plan = preview(client, {"action": "delete", "ids": [rows[0].id]})
    assert (
        client.post(
            "/api/admin/plays/apply", json={"token": plan["token"]}, headers=AUTH
        ).status_code
        == 200
    )
    after = cached_top(db, radio.id, None, None, 25)
    assert next(count for _, title, count in after if title == "Song") == 2
    date = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    plan = preview(
        client,
        {
            "action": "correct",
            "ids": [rows[2].id],
            "title": "Song",
            "artist": "Artist",
            "detected_at": date,
        },
    )
    assert (
        client.post(
            "/api/admin/plays/apply", json={"token": plan["token"]}, headers=AUTH
        ).status_code
        == 200
    )
    assert utc(rows[2].detected_at).isoformat() == date
    assert utc(rows[2].last_seen_at) >= utc(rows[2].detected_at)
