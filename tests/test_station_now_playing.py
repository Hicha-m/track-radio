"""Freshness, exclusions, history preservation and bounded station-card reads."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select

from app.config import settings
from app.models import GlobalExclusion, Play, Station
from app.services.catalog import ensure_entry
from app.services.collector import record_metadata
from app.services.exclusions import canonical_exclusions, exclusion_id, normalized_pair
from app.services.metadata import TrackMetadata as Observation
from app.services.station_now_playing import now_playing

NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)
AUTH = {"Authorization": "Bearer test-admin-token"}


def station(db, name, title="Song", *, artist="Artist", age=20, start_age=120, enabled=True):
    radio = Station(
        name=name, slug=name.lower(), stream_url="https://example.test/live", enabled=enabled
    )
    db.add(radio)
    db.flush()
    play = Play(
        station_id=radio.id,
        artist=artist,
        title=title,
        raw_metadata="preserved original",
        detected_at=NOW - timedelta(seconds=start_age),
        last_seen_at=NOW - timedelta(seconds=age),
        source="json",
    )
    db.add(play)
    db.flush()
    radio.collection_state = {
        "artist": artist,
        "title": title,
        "play_id": play.id,
        "start": int(play.detected_at.timestamp() * 1000),
    }
    entry = ensure_entry(db, artist, title)
    entry.selected = {
        "provider": "itunes",
        "provider_id": title,
        "cover_url": f"https://example.test/{title}.jpg",
        "duration": 210,
    }
    db.commit()
    return radio, play, entry


def hide(db, artist, title):
    pair = normalized_pair(artist, title)
    entry = GlobalExclusion(
        id=exclusion_id(artist, title),
        artist=artist,
        title=title,
        artist_normalized=pair[0],
        title_normalized=pair[1],
    )
    db.add(entry)
    db.commit()
    return entry


def by_station(db, exclusions="", **kwargs):
    return {
        row["station_id"]: row for row in now_playing(db, exclusions, now=NOW, **kwargs)["items"]
    }


def test_current_last_known_missing_and_disabled_are_distinct(db):
    fresh, _, _ = station(db, "Fresh")
    old, _, _ = station(db, "Old", age=600, start_age=620)
    stopped, _, _ = station(db, "Stopped", enabled=False)
    missing = Station(name="Empty", slug="empty", stream_url="https://example.test/live")
    db.add(missing)
    db.commit()
    rows = by_station(db)
    assert rows[fresh.id]["status"] == "current"
    assert rows[fresh.id]["observed_at"] == NOW - timedelta(seconds=20)
    assert rows[fresh.id]["fresh_until"] == NOW + timedelta(seconds=160)
    assert rows[old.id]["status"] == rows[stopped.id]["status"] == "last_known"
    assert rows[old.id]["track"]["title"] == "Song"
    assert rows[missing.id]["status"] == "unavailable" and rows[missing.id]["track"] is None


@pytest.mark.parametrize(
    "age,duration,status",
    [
        (700, 210, "last_known"),
        (1800, 3600, "current"),
        (1800, None, "last_known"),
        (-1, 210, "last_known"),
    ],
)
def test_source_event_age_can_outweigh_a_recent_collection(db, age, duration, status):
    radio, _, entry = station(db, "Source")
    radio.collection_state = {
        **radio.collection_state,
        "start": int((NOW - timedelta(seconds=age)).timestamp() * 1000),
    }
    entry.selected = {**entry.selected, "duration": duration}
    db.commit()
    assert by_station(db)[radio.id]["status"] == status


def test_undated_observation_and_future_dates_do_not_claim_permanent_live_data(db):
    radio, play, entry = station(db, "Undated", start_age=1800)
    radio.collection_state = {**radio.collection_state, "start": None}
    entry.selected = {**entry.selected, "duration": "unknown", "cover_url": "javascript:alert(1)"}
    db.commit()
    row = by_station(db)[radio.id]
    assert row["status"] == "last_known" and row["track"]["metadata"]["cover_url"] is None
    play.last_seen_at = NOW + timedelta(seconds=1)
    db.commit()
    assert by_station(db)[radio.id]["status"] == "unavailable"


@pytest.mark.parametrize("headers", [{}, AUTH])
def test_api_combines_exclusions_before_exposing_titles_covers_or_identifiers(db, client, headers):
    personal, _, _ = station(db, "One", "Personal secret", artist="ÉTOILE")
    global_radio, _, _ = station(db, "Two", "Global secret")
    visible, _, _ = station(db, "Three", "Visible")
    hide(db, "Artist", "Global secret")
    params = {"exclusions": json.dumps([{"artist": "etoile", "title": "PERSONAL SECRET"}])}
    response = client.get("/api/stations/now-playing", params=params, headers=headers)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    data = {row["station_id"]: row for row in response.json()["items"]}
    assert data[personal.id]["status"] == data[global_radio.id]["status"] == "hidden"
    assert data[personal.id]["track"] is data[global_radio.id]["track"] is None
    assert "secret" not in response.text.lower()
    assert data[visible.id]["track"]["title"] == "Visible"
    assert db.query(Play).count() == 3
    assert all(row.raw_metadata == "preserved original" for row in db.scalars(select(Play)))
    # A different user's unfiltered response is independent of this personal filter.
    other = client.get("/api/stations/now-playing").json()["items"]
    assert next(row for row in other if row["station_id"] == personal.id)["track"] is not None


def test_hidden_latest_play_never_falls_back_to_an_older_visible_track(db):
    radio, first, _ = station(db, "Radio", "Earlier")
    second = Play(
        station_id=radio.id,
        artist="Artist",
        title="Latest secret",
        raw_metadata="retained",
        detected_at=NOW,
        last_seen_at=NOW,
        source="icy",
    )
    db.add(second)
    db.flush()
    radio.collection_state = None  # Legacy history uses the same unfiltered latest-row rule.
    db.commit()
    hidden = canonical_exclusions(json.dumps([{"artist": "Artist", "title": "Latest secret"}]))
    row = by_station(db, hidden)[radio.id]
    assert row["status"] == "hidden" and row["track"] is None
    assert db.get(Play, first.id) is not None and db.query(Play).count() == 2


def test_blocked_transition_without_a_play_does_not_resurrect_the_previous_music(db):
    radio, historical, _ = station(db, "Blocked", "Previous")
    excluded = hide(db, "Station", "Announcement")
    # Use the actual collector transition. This must not insert the blocked observation.
    assert record_metadata(db, radio, Observation("Station", "Announcement", "raw", "icy")) == (
        None,
        False,
    )
    assert radio.collection_state["play_id"] is None
    row = by_station(db)[radio.id]
    assert row["status"] == "hidden" and row["track"] is None
    assert db.get(Play, historical.id) is not None and db.query(Play).count() == 1
    db.delete(excluded)
    db.commit()
    assert by_station(db)[radio.id]["status"] == "unavailable"
    # Only a new collector observation restores a visible card, not a fabricated history row.
    assert record_metadata(db, radio, Observation("Station", "Announcement", "raw", "icy"))[1]
    assert db.query(Play).count() == 2


def test_invalid_active_reference_does_not_expose_another_station_or_older_history(db):
    a, first, _ = station(db, "A", "First")
    _, other, _ = station(db, "B", "Other")
    a.collection_state = {**a.collection_state, "play_id": other.id}
    db.commit()
    assert by_station(db)[a.id]["status"] == "unavailable"
    a.collection_state = {**a.collection_state, "play_id": first.id}
    db.delete(first)
    db.commit()
    assert by_station(db)[a.id]["status"] == "unavailable"


def test_bounded_queries_and_lightweight_columns_for_many_radios(db):
    for i in range(12):
        station(db, f"Radio{i}", f"Song{i}")
    queries = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", observe)
    try:
        result = now_playing(db, now=NOW)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", observe)
    assert len(result["items"]) == 12 and len(queries) <= 4
    assert all("raw_metadata" not in sql and "lyrics" not in sql for sql in queries)


def test_freshness_window_accounts_for_throttled_collector_writes(db, monkeypatch):
    monkeypatch.setattr(settings, "last_seen_write_seconds", 600)
    radio, _, _ = station(db, "Throttled", age=300, start_age=400)
    result = now_playing(db, now=NOW)
    assert result["fresh_for_seconds"] >= 600 + settings.poll_interval_seconds
    assert (
        next(row for row in result["items"] if row["station_id"] == radio.id)["status"] == "current"
    )


@pytest.mark.parametrize("value", ["{broken", json.dumps([{"title": "x"}] * 51)])
def test_now_playing_rejects_invalid_personal_filters(client, value):
    assert client.get("/api/stations/now-playing", params={"exclusions": value}).status_code == 422


def test_single_station_filter_does_not_read_or_return_unrelated_stations(db, client):
    a, _, _ = station(db, "First", "One")
    station(db, "Second", "Two")
    result = client.get("/api/stations/now-playing", params={"station_id": a.id})
    assert result.status_code == 200
    assert [item["station_id"] for item in result.json()["items"]] == [a.id]
    assert client.get("/api/stations/now-playing?station_id=999").json()["items"] == []
    for value in ("0", "-1", "wrong"):
        assert (
            client.get("/api/stations/now-playing", params={"station_id": value}).status_code == 422
        )


def test_normalized_exclusions_remain_strict_when_sql_only_provides_lowercase(db, monkeypatch):
    radio, _, _ = station(db, "Unicode", "L’été", artist="ÉTOILE")
    # Exercise the non-SQLite normalization branch, without depending on a database extension.
    monkeypatch.setattr(db.get_bind().dialect, "name", "other")
    exclusions = canonical_exclusions(json.dumps([{"artist": "etoile", "title": "L'ete"}]))
    assert by_station(db, exclusions)[radio.id]["status"] == "hidden"
