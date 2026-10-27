import json
from datetime import UTC, datetime, timedelta

import pytest

from app.models import GlobalExclusion, Play, Station, TrackMetadata
from app.services.broadcast_audit import track_duration
from app.services.collector import record_metadata
from app.services.exclusions import exclusion_id, normalized_pair
from app.services.metadata import TrackMetadata as Observation
from app.services.track_identity import identity

AUTH = {"Authorization": "Bearer test-admin-token"}
START = datetime(2026, 1, 1, tzinfo=UTC)


def station(db, name):
    row = Station(name=name, slug=name.lower(), stream_url="https://example.test/live")
    db.add(row)
    db.flush()
    return row


def play(db, radio, seconds, title="Song", start=None, duration=240):
    key = identity("Artist", title)
    if db.get(TrackMetadata, key) is None:
        db.add(
            TrackMetadata(
                id=key,
                artist="Artist",
                title=title,
                selected={"duration": duration} if duration is not None else None,
            )
        )
        db.flush()
    row = Play(
        station_id=radio.id,
        artist="Artist",
        title=title,
        raw_metadata=json.dumps({"start": start}),
        source="json",
        detected_at=START + timedelta(seconds=seconds),
        last_seen_at=START + timedelta(seconds=seconds),
    )
    db.add(row)
    db.flush()
    return row


def report(client, **params):
    result = client.get("/api/admin/plays/audit", params=params, headers=AUTH)
    assert result.status_code == 200, result.text
    return result.json()


def test_all_stations_duration_source_and_consecutive_signals_are_read_only(db, client):
    first, second = station(db, "One"), station(db, "Two")
    a = play(db, first, 0, start=1000)
    play(db, first, 30, title="Other")
    b = play(db, first, 200, start=1000)
    replay = play(db, first, 900, start=9000)
    play(db, second, 0)
    play(db, second, 100)
    db.commit()
    assert client.get("/api/admin/plays/audit").status_code == 401
    data = report(client)
    pair = next(item for item in data["items"] if item["current"]["id"] == b.id)
    assert pair["previous"]["id"] == a.id
    assert pair["reasons"] == ["source", "duration"]
    assert pair["duration"] == 240 and pair["threshold_seconds"] == 225
    assert pair["gap_seconds"] == 200
    real = next(item for item in data["items"] if item["current"]["id"] == replay.id)
    assert real["reasons"] == ["consecutive"]  # Weak signal, never automatic deletion.
    assert len(data["stations"]) == 2
    assert sum(s["plays"] for s in data["stations"]) == 6
    assert db.query(Play).count() == 6
    assert db.get(Play, replay.id).raw_metadata == '{"start": 9000}'


def test_duration_margin_and_unknown_fallback_are_adjustable(db, client):
    radio = station(db, "One")
    play(db, radio, 0)
    play(db, radio, 10, "Interlude")
    play(db, radio, 230)
    play(db, radio, 500, "Unknown", duration=None)
    play(db, radio, 510, "Another interlude")
    play(db, radio, 670, "Unknown", duration=None)
    db.commit()
    assert report(client, reason="duration")["total"] == 0
    assert report(client, reason="duration", margin=0)["total"] == 1
    assert report(client, reason="unknown")["total"] == 1
    assert report(client, reason="unknown", fallback=150)["total"] == 0


def test_source_identity_survives_intervening_distinct_event_and_long_gap(db, client):
    radio = station(db, "One")
    original = play(db, radio, 0, start=1000)
    play(db, radio, 900, start=2000)
    repeat = play(db, radio, 2000, start=1000)
    db.commit()
    data = report(client, reason="source")
    assert data["total"] == 1
    assert data["items"][0]["previous"]["id"] == original.id
    assert data["items"][0]["current"]["id"] == repeat.id
    assert data["items"][0]["reasons"] == ["source"]


def test_date_filter_keeps_preceding_context_and_station_isolation(db, client):
    a, b = station(db, "One"), station(db, "Two")
    original = play(db, a, 0)
    play(db, b, 100)
    duplicate = play(db, a, 110)
    db.commit()
    data = report(
        client,
        station_id=a.id,
        since=(START + timedelta(seconds=100)).isoformat(),
        until=(START + timedelta(seconds=200)).isoformat(),
    )
    assert data["total"] == 1 and data["stations"][0]["plays"] == 1
    assert data["items"][0]["previous"]["id"] == original.id
    assert data["items"][0]["current"]["id"] == duplicate.id


def test_exclusions_apply_before_results_and_counts_without_hiding_context(db, client):
    radio = station(db, "One")
    play(db, radio, 0)
    play(db, radio, 50, "Global")
    play(db, radio, 100)
    play(db, radio, 200, "Global")
    play(db, radio, 300, "Personal")
    play(db, radio, 350, "Personal")
    artist, title = normalized_pair("Artist", "Global")
    db.add(
        GlobalExclusion(
            id=exclusion_id("Artist", "Global"),
            artist="Artist",
            title="Global",
            artist_normalized=artist,
            title_normalized=title,
        )
    )
    db.commit()
    data = report(client, exclusions=json.dumps([{"artist": "Artist", "title": "Personal"}]))
    assert data["stations"][0]["plays"] == 2
    assert data["total"] == 1
    assert data["items"][0]["reasons"] == ["duration"]
    assert db.query(Play).count() == 6


def test_pagination_snapshot_and_preview_existing_correction(db, client):
    radio = station(db, "One")
    for n in range(28):
        play(db, radio, n * 10, start=1000)
    db.commit()
    data = report(client)
    assert data["total"] == 27 and len(data["items"]) == 25 and data["has_more"]
    play(db, radio, 300, start=1000)
    db.commit()
    page = report(client, page=2, snapshot_id=data["snapshot_id"])
    assert page["total"] == 27 and len(page["items"]) == 2 and not page["has_more"]
    assert not {i["current"]["id"] for i in data["items"]} & {
        i["current"]["id"] for i in page["items"]
    }
    pair = data["items"][0]
    preview = client.post(
        "/api/admin/plays/preview",
        headers=AUTH,
        json={
            "action": "merge",
            "ids": [pair["previous"]["id"], pair["current"]["id"]],
            "keep_id": pair["previous"]["id"],
        },
    )
    assert preview.status_code == 200, preview.text
    assert len(preview.json()["before"]) == 2 and len(preview.json()["after"]) == 1
    assert db.query(Play).count() == 29


def test_merging_current_duplicate_preserves_collector_state_and_real_rebroadcast(db, client):
    radio = station(db, "One")
    metadata = Observation("Artist", "Song", '{"start": 1000}', "json", 1000)
    first, _ = record_metadata(db, radio, metadata)
    first.detected_at = datetime.now(UTC) - timedelta(seconds=60)
    first.last_seen_at = first.detected_at
    duplicate = play(db, radio, 100, start=1000)
    duplicate.detected_at = first.detected_at + timedelta(seconds=20)
    duplicate.last_seen_at = duplicate.detected_at
    radio.collection_state = {**radio.collection_state, "play_id": duplicate.id}
    db.commit()
    plan = client.post(
        "/api/admin/plays/preview",
        headers=AUTH,
        json={"action": "merge", "ids": [first.id, duplicate.id], "keep_id": first.id},
    ).json()
    result = client.post("/api/admin/plays/apply", headers=AUTH, json={"token": plan["token"]})
    assert result.status_code == 200, result.text
    assert radio.collection_state["play_id"] == first.id
    assert not record_metadata(db, radio, metadata)[1]
    assert db.query(Play).count() == 1
    assert record_metadata(
        db, radio, Observation("Artist", "Song", '{"start": 2000}', "json", 2000)
    )[1]
    assert first.raw_metadata == '{"start": 1000}'


@pytest.mark.parametrize("value", [None, {}, "240", True, -1, 0, float("inf"), 100000])
def test_invalid_duration_is_unknown(value):
    assert track_duration({"duration": value}) is None


@pytest.mark.parametrize(
    "params",
    [
        {"margin": -1},
        {"fallback": 0},
        {"reason": "invalid"},
        {"page": 0},
        {"since": "2026-01-01T00:00:00"},
        {"since": START.isoformat(), "until": START.isoformat()},
    ],
)
def test_invalid_filters_rejected(client, params):
    assert client.get("/api/admin/plays/audit", params=params, headers=AUTH).status_code == 422
