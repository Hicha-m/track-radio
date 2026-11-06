"""Reproducible synthetic wide-row benchmark; never reads a production database."""

import argparse
import json
import statistics
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import make_engine
from app.metrics_api import MetricsFilters, compute
from app.models import Base, GlobalExclusion, Play, Station, TrackMetadata
from app.services.exclusions import exclusion_id, normalized_pair
from app.services.explorer import ExploreFilters, query_results
from app.services.stats import _cache, cached_top
from app.services.track_identity import identity

INDEXES = {
    "ix_plays_chart_cover": "plays (artist, title, detected_at, station_id, track_id)",
    "ix_plays_period_cover": "plays (detected_at, artist, title, station_id, track_id)",
    "ix_track_metadata_genre_cover": "track_metadata (id, primary_genre)",
}


def seed(engine, count):
    now = datetime(2026, 11, 6, 12, tzinfo=UTC)
    pairs = []
    for i in range(4000):
        artist, title = (f"Artiste {i % 500}", f"Titre {i}")
        pairs.append((artist, title, identity(artist, title)))
    with engine.begin() as conn:
        conn.execute(
            Station.__table__.insert(),
            [
                dict(
                    id=i + 1,
                    name=f"Station {i}",
                    slug=f"station-{i}",
                    stream_url="https://example.test/live",
                )
                for i in range(9)
            ],
        )
        conn.execute(
            TrackMetadata.__table__.insert(),
            [
                dict(
                    id=k,
                    artist=a,
                    title=t,
                    primary_genre="Synthetic",
                    lyrics={"plain": "fixture " * 1000},
                )
                for a, t, k in pairs
            ],
        )
        conn.execute(
            GlobalExclusion.__table__.insert(),
            [
                dict(
                    id=exclusion_id(a, t),
                    artist=a,
                    title=t,
                    artist_normalized=normalized_pair(a, t)[0],
                    title_normalized=normalized_pair(a, t)[1],
                )
                for a, t, _ in pairs[:30]
            ],
        )
        for start in range(0, count, 250):
            rows = []
            for i in range(start, min(start + 250, count)):
                a, t, key = pairs[i % len(pairs)]
                at = now - timedelta(seconds=i * 50)
                rows.append(
                    dict(
                        id=i + 1,
                        station_id=i % 9 + 1,
                        artist=a,
                        title=t,
                        track_id=key,
                        raw_metadata="fixture " * 1024,
                        source="json",
                        detected_at=at,
                        last_seen_at=at,
                    )
                )
            conn.execute(Play.__table__.insert(), rows)
    return now


def measure(engine, now, repetitions=3):
    cases = {
        "chart_7d": lambda db: list(cached_top(db, None, now - timedelta(days=7), now, 20)),
        "chart_all": lambda db: list(cached_top(db, None, None, None, 20)),
        "search_tracks": lambda db: query_results(db, ExploreFilters(view="tracks", page_size=25)),
        "summary_24h": lambda db: compute(db, "summary", MetricsFilters(), now),
    }
    results, output = {}, {}
    for name, run in cases.items():
        durations = []
        for _ in range(repetitions):
            _cache.clear()
            with Session(engine) as db:
                started = time.perf_counter()
                results[name] = run(db)
                durations.append(time.perf_counter() - started)
        output[name] = {"seconds": durations, "median": statistics.median(durations)}
    return results, output


def benchmark(count=48000):
    with tempfile.TemporaryDirectory(prefix="radio-profile-lab-") as directory:
        engine = make_engine(f"sqlite:///{directory}/synthetic.db")
        Base.metadata.create_all(engine)
        now = seed(engine, count)
        baseline_results, before = measure(engine, now)
        stages = {"before": before}
        with engine.begin() as conn:
            conn.execute(text("ANALYZE"))
        results, stages["analyze_only"] = measure(engine, now)
        assert baseline_results == results
        for name, expression in reversed(list(INDEXES.items())):
            with engine.begin() as conn:
                conn.execute(text(f"CREATE INDEX {name} ON {expression}"))
                conn.execute(text(f"ANALYZE {name}"))
            results, stages[name] = measure(engine, now)
            assert baseline_results == results, "Optimization changed results"
        output = {
            "environment": "synthetic local, OS cache warm, SQLite cache bounded",
            "plays": count,
            "repetitions": 3,
            "all_results_identical": True,
            "stages": stages,
            "database_bytes": Path(directory, "synthetic.db").stat().st_size,
        }
        engine.dispose()
        return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plays", type=int, default=48000)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.plays), indent=2, default=str))
