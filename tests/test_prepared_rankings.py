import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, event, func, select, update

from app.models import GlobalExclusion, Play, PlayCount, RankingCount, Station
from app.services.exclusions import exclusion_id, normalized_pair
from app.services.prepared_rankings import PERIODS, read, refresh
from app.services.stats import cached_top

NOW = datetime(2026, 11, 10, 12, 23, 17, tzinfo=UTC)


@pytest.fixture
def history(db):
    stations = [
        Station(name=f"Radio {n}", slug=f"radio-{n}", stream_url="https://example.test")
        for n in range(2)
    ]
    db.add_all(stations)
    db.flush()
    for i, delta in enumerate(
        [0, 1, 3600, 86400 - 1, 86400, 7 * 86400 - 1, 7 * 86400, 30 * 86400, 31 * 86400]
    ):
        at = NOW - timedelta(seconds=delta)
        db.add(
            Play(
                station_id=stations[i % 2].id,
                artist="ARTIST" if i % 2 else "Artist",
                title="Song" if i % 3 else "Other",
                detected_at=at,
                last_seen_at=at,
                source="json",
                raw_metadata=f"original-{i}",
            )
        )
    db.commit()
    return stations


def factory(db):
    @contextmanager
    def sessions():
        yield db

    return sessions


def test_counts_follow_insert_correction_bulk_delete_and_rollback(db, history):
    assert db.scalar(select(func.sum(PlayCount.plays))) == 9
    row = db.scalar(select(Play).order_by(Play.id))
    old = row.title
    db.execute(
        update(Play)
        .where(Play.id == row.id)
        .values(title="Corrected", track_id=None, detected_at=NOW - timedelta(days=2))
    )
    db.commit()
    assert db.scalar(select(func.sum(PlayCount.plays))) == 9
    assert db.scalar(select(PlayCount.plays).where(PlayCount.title == "Corrected")) == 1
    db.execute(delete(Play).where(Play.id == row.id))
    db.flush()
    assert db.scalar(select(func.sum(PlayCount.plays))) == 8
    db.rollback()
    assert db.scalar(select(func.sum(PlayCount.plays))) == 9
    db.execute(delete(Play))
    db.commit()
    assert db.scalar(select(func.count()).select_from(PlayCount)) == 0
    assert old in {"Song", "Other"}


@pytest.mark.parametrize("period", PERIODS)
def test_exact_boundaries_station_filters_and_legacy_identity(db, history, period):
    db.execute(update(Play).where(Play.id % 3 == 0).values(track_id=None))
    db.commit()
    refresh(factory(db), NOW)
    cutoff = NOW.replace(tzinfo=None)
    days = PERIODS[period]
    since = cutoff - timedelta(days=days) if days else None
    for stations in [(), (history[0].id,), tuple(s.id for s in history)]:
        expected = cached_top(db, None, since, cutoff, 100, "", stations)
        result = read(db, period, 100, station_ids=stations, now=NOW)
        assert [(r["artist"], r["title"], r["plays"]) for r in result["items"]] == list(expected)
        assert result["until"] == NOW and not result["stale"]
    assert db.scalar(select(func.count()).select_from(Play)) == 9


@pytest.mark.parametrize("admin", [False, True])
def test_visibility_invalidation_empty_and_stale_response(db, client, history, admin):
    refresh(factory(db), NOW)
    headers = {"Authorization": "Bearer test-admin-token"} if admin else {}
    params = {"period": "7", "exclusions": json.dumps([{"artist": "Artist", "title": "Other"}])}
    response = client.get("/api/stats/prepared", params=params, headers=headers)
    assert response.status_code == 200
    assert response.json()["stale"]
    assert all(t["title"] != "Other" for t in response.json()["items"])
    artist, title = normalized_pair("Artist", "Song")
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
    assert client.get("/api/stats/prepared", params=params, headers=headers).status_code == 503
    refresh(factory(db), NOW)
    response = client.get("/api/stats/prepared", params=params, headers=headers)
    assert response.status_code == 200 and response.json()["items"] == []
    assert db.scalar(select(func.count()).select_from(Play)) == 9


def test_reader_does_not_touch_history_and_refresh_rollback_preserves_snapshot(db, history):
    refresh(factory(db), NOW)
    before = read(db, "7", 20, now=NOW)
    statements = []

    def capture(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", capture)
    try:
        assert read(db, "7", 20, now=NOW) == before
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture)
    assert not any("FROM plays" in sql or "FROM play_counts" in sql for sql in statements)
    db.execute(delete(RankingCount))
    db.rollback()
    assert read(db, "7", 20, now=NOW) == before


def test_staging_failure_leaves_published_views_intact(db, history, monkeypatch):
    from app.services import prepared_rankings

    refresh(factory(db), NOW)
    previous = read(db, "7", 20, now=NOW)
    original = prepared_rankings.raw_counts
    calls = 0

    def fail_after_one_period(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated preparation failure")
        return original(*args)

    monkeypatch.setattr(prepared_rankings, "raw_counts", fail_after_one_period)
    with pytest.raises(RuntimeError):
        refresh(factory(db), NOW + timedelta(seconds=30))
    assert read(db, "7", 20, now=NOW) == previous
    assert db.scalar(select(func.count(func.distinct(RankingCount.generation)))) == 1
