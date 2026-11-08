"""Read-only deployment check. Never print station data or upstream results."""

import os
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import CollectionEvent, CollectionStatus, Station


def state(db, revision):
    # The collector visits enabled stations by ID; inspect the first one only.
    station_id = db.scalar(
        select(Station.id).where(Station.enabled.is_(True)).order_by(Station.id).limit(1)
    )
    if station_id is None:
        return "disabled"
    status = db.get(CollectionStatus, station_id)
    if (
        not status
        or not status.last_attempt_at
        or (status.runtime or {}).get("revision") != revision
    ):
        return "waiting"
    recent = db.scalar(
        select(CollectionEvent.id)
        .where(
            CollectionEvent.station_id == station_id,
            CollectionEvent.kind == "collection",
            CollectionEvent.round_id.is_not(None),
            CollectionEvent.started_at >= status.last_attempt_at,
            CollectionEvent.finished_at >= datetime.now(UTC) - timedelta(minutes=5),
        )
        .limit(1)
    )
    return "ready" if recent else "waiting"


def main():
    deadline = time.monotonic() + settings.request_timeout_seconds + 30
    while True:
        with SessionLocal() as db:
            result = state(db, os.getenv("APP_REVISION", "development"))
        if result == "ready":
            print("Collection diagnostics verified: worker persisted a completed attempt")
            return
        if result == "disabled":
            print("Collection diagnostics check skipped: no enabled stations")
            return
        if time.monotonic() >= deadline:
            raise SystemExit("Collection diagnostics missing for current worker revision")
        time.sleep(1)


if __name__ == "__main__":
    main()
