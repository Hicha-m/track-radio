"""Bounded read-only loading profile. Print timings/plans, never SQL values or results."""

import json
import time
import urllib.request
from datetime import UTC, datetime, timedelta

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.admin_api import dashboard_summary
from app.config import settings
from app.database import engine
from app.main import top_tracks
from app.services.exclusions import ExclusionFilters
from app.services.station_now_playing import now_playing


def profile():
    if engine.dialect.name != "sqlite":
        raise SystemExit("This diagnostic requires SQLite")
    since = datetime.now(UTC).replace(microsecond=0) - timedelta(days=7)
    captured, timings = [], []
    active = [False]
    started = [0.0]

    def before(conn, cursor, statement, parameters, context, many):
        if active[0]:
            started[0] = time.perf_counter()
            if statement.lstrip().upper().startswith("SELECT"):
                captured.append((statement, parameters))

    def after(*args):
        if active[0]:
            timings.append(time.perf_counter() - started[0])

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA query_only=ON")
            raw = conn.connection.driver_connection
            deadline = [time.monotonic() + 20]
            raw.set_progress_handler(lambda: int(time.monotonic() > deadline[0]), 10000)
            try:
                with Session(bind=conn) as db:

                    def top(start):
                        return top_tracks(db, None, start, None, 20, "", "")

                    cases = [
                        ("top_process_cold", lambda: top(since)),
                        ("top_identical_warm", lambda: top(since)),
                        ("top_shifted_period", lambda: top(since + timedelta(seconds=30))),
                        ("now_playing_first", lambda: now_playing(db)),
                        ("now_playing_repeat", lambda: now_playing(db)),
                        (
                            "admin_dashboard_first",
                            lambda: dashboard_summary(db, ExclusionFilters()),
                        ),
                        (
                            "admin_dashboard_repeat",
                            lambda: dashboard_summary(db, ExclusionFilters()),
                        ),
                    ]
                    for name, call in cases:
                        captured.clear()
                        timings.clear()
                        deadline[0] = time.monotonic() + 20
                        start = time.perf_counter()
                        active[0] = True
                        try:
                            call()  # Discard results: never serialize production content.
                            error = None
                        except Exception as exc:
                            error = type(exc).__name__
                        finally:
                            active[0] = False
                        result = {
                            "case": name,
                            "total_ms": round((time.perf_counter() - start) * 1000, 2),
                            "cursor_execute_ms": round(sum(timings) * 1000, 2),
                            "statements": len(captured),
                            "error": error,
                            "plans": [],
                        }
                        deadline[0] = time.monotonic() + 10
                        for sql, params in captured[:12]:
                            try:
                                rows = conn.exec_driver_sql("EXPLAIN QUERY PLAN " + sql, params)
                                result["plans"].append([r[3] for r in rows])
                            except Exception as exc:
                                result["plans"].append([type(exc).__name__])
                        print(json.dumps(result), flush=True)
            finally:
                raw.set_progress_handler(None, 0)
                conn.rollback()
                conn.exec_driver_sql("PRAGMA query_only=OFF")
    finally:
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)

    # Existing API process: first sample has unknown cache state. No cache eviction.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    paths = {
        "top": "/api/stats/top?limit=20&since=" + since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "now_playing": "/api/stations/now-playing",
        "artwork": "/api/stations/2/artwork",
        "admin_dashboard": "/api/admin/dashboard",
    }
    for name, path in paths.items():
        for sample in range(2):
            request = urllib.request.Request("http://127.0.0.1:8000" + path)
            if name == "admin_dashboard":
                request.add_header(
                    "Authorization", "Bearer " + settings.admin_token.get_secret_value()
                )
            start = time.perf_counter()
            try:
                with opener.open(request, timeout=20) as response:
                    response.read(2 * 1024 * 1024)
                    status, error = response.status, None
            except Exception as exc:
                status, error = getattr(exc, "code", None), type(exc).__name__
            print(
                json.dumps(
                    {
                        "route": name,
                        "sample": sample + 1,
                        "status": status,
                        "error": error,
                        "total_ms": round((time.perf_counter() - start) * 1000, 2),
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    profile()
