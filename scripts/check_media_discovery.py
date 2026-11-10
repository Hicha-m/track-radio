"""Read-only post-deployment flags; no titles, provider payloads or private filters."""

import json
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import MediaDiscovery, TrackMetadata, WorkerState
from app.services.media_discovery import visible


def check(factory=SessionLocal):
    with factory() as db:
        state = db.get(WorkerState, "discovery")
        recent = bool(
            state
            and 0
            <= (datetime.now(UTC) - state.heartbeat_at.replace(tzinfo=UTC)).total_seconds()
            < 180
        )
        eligible = db.scalar(
            select(func.count())
            .select_from(MediaDiscovery)
            .join(TrackMetadata, TrackMetadata.id == MediaDiscovery.track_id)
            .where(*visible(db))
        )
        checked = db.scalar(
            select(func.count())
            .select_from(MediaDiscovery)
            .where(MediaDiscovery.checked_at.is_not(None))
        )
        return {
            "worker_recent": recent,
            "queue_has_visible_tasks": bool(eligible),
            "completed_attempt_seen": bool(checked),
            "latest_round_error": bool(state and state.state == "error"),
        }


if __name__ == "__main__":
    flags = check()
    print("Media discovery flags: " + json.dumps(flags, sort_keys=True))
    if (
        not flags["worker_recent"]
        or flags["queue_has_visible_tasks"]
        and not flags["completed_attempt_seen"]
    ):
        raise SystemExit("Media discovery has not demonstrated progress")
