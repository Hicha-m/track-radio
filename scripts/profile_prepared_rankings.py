"""Read-only comparison at the prepared cutoff; never print musical data or filters."""

import json
from datetime import UTC, timedelta
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import engine
from app.models import RankingState, Station
from app.services.prepared_rankings import read
from app.services.stats import cached_top


def profile():
    with engine.connect() as connection:
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA query_only=ON")
            connection.exec_driver_sql("BEGIN")
        with Session(bind=connection) as db:
            state = db.get(RankingState, "7")
            if state is None:
                raise SystemExit("Prepared ranking not available yet")
            cutoff = state.calculated_at.replace(tzinfo=UTC)
            station = db.scalar(select(Station.id).order_by(Station.id).limit(1))
            initial = read(db, "7", 100, now=cutoff)
            if initial is None:
                raise SystemExit("Prepared ranking requires refresh after exclusions changed")
            first = initial["items"][0] if initial["items"] else None
            personal = (
                json.dumps([{"artist": first["artist"], "title": first["title"]}]) if first else ""
            )
            samples = []
            for label, hidden, ids in [
                ("all", "", ()),
                ("personal", personal, ()),
                ("station", "", (station,)),
            ]:
                started = perf_counter()
                raw = cached_top(db, None, cutoff - timedelta(days=7), cutoff, 100, hidden, ids)
                raw_ms = (perf_counter() - started) * 1000
                started = perf_counter()
                prepared = read(db, "7", 100, hidden, station_ids=ids, now=cutoff)
                prepared_ms = (perf_counter() - started) * 1000
                expected = [{"artist": a, "title": t, "plays": n} for a, t, n in raw]
                samples.append(
                    {
                        "case": label,
                        "raw_ms": round(raw_ms, 2),
                        "prepared_ms": round(prepared_ms, 2),
                        "equal": prepared is not None and prepared["items"] == expected,
                        "rows": len(expected),
                    }
                )
            print(json.dumps({"prepared_ranking_comparison": samples}))
            if not all(row["equal"] for row in samples):
                raise SystemExit(
                    "Prepared comparison mismatch; investigate before declaring success"
                )
        connection.rollback()


if __name__ == "__main__":
    profile()
