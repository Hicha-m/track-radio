"""Bounded read-only production profiling. Output contains no query results or parameters."""

import argparse
import cProfile
import json
import pstats
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.metrics_api import MetricsFilters, compute
from app.services.explorer import ExploreFilters, query_results
from app.services.search_text import normalize_search, register_search
from app.services.stats import cached_top


def read_only_engine(path, deadline):
    def connect():
        connection = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True, timeout=2)
        register_search(connection, None)
        connection.execute("PRAGMA query_only=ON")
        connection.set_progress_handler(lambda: int(time.perf_counter() > deadline[0]), 10000)
        return connection

    return create_engine("sqlite://", creator=connect, poolclass=NullPool)


def profile(path, budget=20):
    deadline = [float("inf")]
    engine = read_only_engine(path, deadline)
    statements = []
    started_queries = []

    @event.listens_for(engine, "before_cursor_execute")
    def before(conn, cursor, statement, parameters, context, executemany):
        started_queries.append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def after(conn, cursor, statement, parameters, context, executemany):
        duration = time.perf_counter() - started_queries.pop()
        if statement.lstrip().upper().startswith("SELECT"):
            # EXPLAIN emits only table/index operations, never parameters or returned rows.
            plan = conn.connection.cursor()
            try:
                plan.execute("EXPLAIN QUERY PLAN " + statement, parameters)
                details = [row[3] for row in plan.fetchall()]
            finally:
                plan.close()
            statements.append({"seconds": round(duration, 4), "plan": details})

    now = datetime.now(UTC).replace(second=0, microsecond=0)
    cases = {
        "chart_7d": lambda db: cached_top(db, None, now - timedelta(days=7), now, 20),
        "chart_all": lambda db: cached_top(db, None, None, None, 20),
        "search_tracks": lambda db: query_results(db, ExploreFilters(view="tracks", page_size=25)),
        "summary_24h": lambda db: compute(db, "summary", MetricsFilters(), now),
    }
    output = {"environment": "isolated container, live volume read-only", "cases": {}}
    for name, run in cases.items():
        statements.clear()
        started_queries.clear()
        deadline[0] = time.perf_counter() + budget
        profiler = cProfile.Profile()
        start = time.perf_counter()
        error = None
        try:
            with Session(engine) as db:
                profiler.enable()
                run(db)
        except Exception as failure:
            error = type(failure).__name__
        finally:
            profiler.disable()
        stats = pstats.Stats(profiler)
        top = sorted(stats.stats.items(), key=lambda entry: entry[1][3], reverse=True)[:16]
        output["cases"][name] = {
            "seconds": round(time.perf_counter() - start, 4),
            "error_type": error,
            "budget_seconds": budget,
            "sql": list(statements),
            "normalizer_cache": normalize_search.cache_info()._asdict(),
            "functions": [
                {
                    "function": f"{Path(key[0]).name}:{key[1]}:{key[2]}",
                    "calls": value[1],
                    "self_seconds": round(value[2], 4),
                    "cumulative_seconds": round(value[3], 4),
                }
                for key, value in top
            ],
        }
    engine.dispose()
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="/app/data/radio.db")
    args = parser.parse_args()
    print(json.dumps(profile(args.database), indent=2))
