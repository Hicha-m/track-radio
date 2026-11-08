"""Release-level invariants: restart, read-only observation and truthful release flags."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.models import CollectionStatus, Station
from scripts.observe_collection import assess, snapshot


def test_restart_preserves_failures_after_deadline_has_expired(db, monkeypatch):
    from app.worker import restore_schedule

    db.add(Station(id=1, name="Fixture", slug="fixture", stream_url="https://example.test"))
    db.flush()
    db.add(
        CollectionStatus(
            station_id=1,
            consecutive_errors=4,
            next_attempt_at=datetime.now(UTC) - timedelta(seconds=10),
            outcome="http_error",
        )
    )
    db.commit()
    monkeypatch.setattr("app.worker.SessionLocal", sessionmaker(bind=db.get_bind()))
    retries, _ = restore_schedule()
    assert retries[1][0] == 4


def test_observation_does_not_read_music_or_mutate_state(db):
    db.add(Station(id=1, name="Skyrock", slug="skyrock", stream_url="https://example.test"))
    db.flush()
    db.add(CollectionStatus(station_id=1, attempts=3, errors=1))
    db.commit()
    statements = []

    def record(conn, cursor, statement, *args):
        statements.append(statement.lower())

    event.listen(db.get_bind(), "before_cursor_execute", record)
    try:
        result = snapshot(db)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", record)
    assert result["stations"][1]["attempts"] == 3
    assert len(statements) == 2
    assert not any(
        word in sql
        for sql in statements
        for word in ("artist", "raw_metadata", " title", "update ", "delete ", "insert ")
    )


def test_heartbeat_and_other_radios_do_not_prove_skyrock_recovery():
    def sample(attempts=0, changed=None, observed=None, errors=0):
        return {
            "worker_recent": True,
            "stations": {
                1: {
                    "skyrock": True,
                    "attempts": attempts,
                    "changed": changed,
                    "observed": observed,
                    "errors": errors,
                    "waiting": True,
                },
                2: {
                    "skyrock": False,
                    "attempts": attempts,
                    "changed": None,
                    "observed": None,
                    "errors": 0,
                    "waiting": False,
                },
            },
        }

    now = datetime.now(UTC)
    initial = sample(1, now, now)
    failure = sample(2, now, now, 1)
    result = assess([initial, failure])
    assert result["worker_recent"] and result["other_stations_progressed"]
    assert result["skyrock_errors_seen"]
    assert not result["skyrock_multiple_changes"] and not result["skyrock_observations_progressed"]
    first = sample(3, now + timedelta(seconds=1), now + timedelta(seconds=1))
    second = sample(4, now + timedelta(seconds=2), now + timedelta(seconds=2))
    assert not assess([initial, first])["skyrock_multiple_changes"]
    assert assess([initial, first, second])["skyrock_multiple_changes"]
