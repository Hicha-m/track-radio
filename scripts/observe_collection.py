"""Bounded read-only post-deploy observation; output validation flags, never source data."""

import argparse
import json
import time
from datetime import UTC, datetime

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import CollectionStatus, Station, WorkerState
from app.services.collection_diagnostics import utc


def snapshot(db):
    rows = db.execute(
        select(
            Station.id,
            Station.slug,
            CollectionStatus.attempts,
            CollectionStatus.last_change_at,
            CollectionStatus.last_observation_at,
            CollectionStatus.errors,
            CollectionStatus.next_attempt_at,
        )
        .outerjoin(CollectionStatus, CollectionStatus.station_id == Station.id)
        .where(Station.enabled.is_(True))
        .order_by(Station.id)
        .limit(settings.max_stations)
    ).all()
    worker = db.get(WorkerState, "collector")
    now = datetime.now(UTC)
    return {
        "worker_recent": bool(
            worker
            and 0
            <= (now - utc(worker.heartbeat_at)).total_seconds()
            <= max(120, settings.poll_interval_seconds * 3)
        ),
        "stations": {
            r.id: {
                "skyrock": r.slug == "skyrock",
                "attempts": r.attempts or 0,
                "changed": r.last_change_at,
                "observed": r.last_observation_at,
                "errors": r.errors or 0,
                "waiting": bool(r.next_attempt_at and utc(r.next_attempt_at) > now),
            }
            for r in rows
        },
    }


def assess(samples):
    initial, final = samples[0], samples[-1]
    progressed = False
    other_progressed = False
    skyrock_changes = set()
    observations = set()
    source_errors = False
    for sample in samples:
        for station_id, row in sample["stations"].items():
            baseline = initial["stations"].get(station_id)
            if baseline and row["attempts"] > baseline["attempts"]:
                progressed = True
                other_progressed |= not row["skyrock"]
            if row["skyrock"]:
                if baseline and row["errors"] > baseline["errors"]:
                    source_errors = True
                if row["changed"]:
                    skyrock_changes.add(row["changed"])
                if row["observed"]:
                    observations.add(row["observed"])
    # Count changes after the initial snapshot, not a pre-existing event as a transition.
    initial_changes = {r["changed"] for r in initial["stations"].values() if r["skyrock"]}
    initial_observations = {r["observed"] for r in initial["stations"].values() if r["skyrock"]}
    return {
        "worker_recent": final["worker_recent"],
        "attempts_progressed": progressed,
        "other_stations_progressed": other_progressed,
        "skyrock_multiple_changes": len(skyrock_changes - initial_changes) >= 2,
        "skyrock_observations_progressed": bool(observations - initial_observations),
        "skyrock_errors_seen": source_errors,
        "skyrock_waiting": any(r["waiting"] for r in final["stations"].values() if r["skyrock"]),
    }


def observe(seconds=360, interval=30):
    samples = []
    deadline = time.monotonic() + seconds
    while True:
        with SessionLocal() as db:
            samples.append(snapshot(db))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return assess(samples)
        time.sleep(min(interval, remaining))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, choices=range(0, 601), default=360)
    args = parser.parse_args()
    result = observe(args.seconds)
    print("Collection observation flags: " + json.dumps(result, sort_keys=True))
    if not result["worker_recent"]:
        raise SystemExit("Collector heartbeat did not remain recent")
    # An unchanged or excluded source is not itself a release failure.
    if not result["skyrock_multiple_changes"]:
        print("Skyrock transitions and antenna-latency comparison remain unconfirmed")


if __name__ == "__main__":
    main()
