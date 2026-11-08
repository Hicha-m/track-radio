"""A reversible index-only migration must preserve history and filtered analytics."""

import json
from datetime import UTC, datetime, timedelta
from importlib import import_module

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_mock_engine, inspect, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import make_engine
from app.metrics_api import MetricsFilters, compute
from app.models import Base, GlobalExclusion, Play, Station
from app.services.catalog import ensure_entry
from app.services.exclusions import canonical_exclusions, exclusion_id, normalized_pair
from app.services.explorer import ExploreFilters, query_results
from app.services.stats import _cache, cached_top

INDEX_NAMES = {"ix_plays_chart_cover", "ix_plays_period_cover", "ix_track_metadata_genre_cover"}


def snapshot(engine, now):
    hidden = canonical_exclusions(json.dumps([{"artist": "Artiste", "title": "Personnel"}]))
    _cache.clear()
    with Session(engine) as db:
        history = list(
            db.execute(
                select(
                    Play.id, Play.artist, Play.title, Play.raw_metadata, Play.detected_at
                ).order_by(Play.id)
            )
        )
        results = []
        for exclusions in ("", hidden):
            ranked = cached_top(db, None, None, None, 20, exclusions)
            assert ("Artiste", "Visible", 4) in ranked  # Four genuine broadcasts remain four.
            assert all(row.title != "L’été" for row in ranked)
            assert any(row.title == "Personnel" for row in ranked) == (not exclusions)
            assert compute(db, "summary", MetricsFilters(exclusions=exclusions), now)["total"] == (
                6 if exclusions else 8
            )
            for stations in ((), (1,), (1, 2)):
                results.append(
                    list(
                        cached_top(db, None, now - timedelta(days=7), now, 20, exclusions, stations)
                    )
                )
            for page in (1, 2):
                results.append(
                    query_results(
                        db,
                        ExploreFilters(
                            view="tracks", exclusions=exclusions, page_size=2, page=page
                        ),
                    )
                )
            for kind in ("summary", "genres", "repetitions", "diversity", "overlap", "offsets"):
                results.append(
                    compute(
                        db,
                        kind,
                        MetricsFilters(exclusions=exclusions, station_a=1, station_b=2),
                        now,
                    )
                )
        return history, results


def test_covering_migration_preserves_exclusions_real_repeats_and_history(tmp_path, monkeypatch):
    path = tmp_path / "analytics.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{path}")
    engine = make_engine(settings.database_url)
    # Isolate the index migration from later column migrations used by today's ORM.
    Base.metadata.create_all(engine)
    migration = import_module("migrations.versions.0013_stats_covering_indexes")

    def migrate(upgrade):
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                (migration.upgrade if upgrade else migration.downgrade)()

    migrate(False)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    with Session(engine) as db:
        db.add_all(
            [
                Station(
                    id=i,
                    name=f"Station {i}",
                    slug=f"station-{i}",
                    stream_url="https://example.test/live",
                )
                for i in (1, 2)
            ]
        )
        db.flush()
        for index, (artist, title) in enumerate(
            (
                (None, "Inconnu"),
                ("Artiste", "Visible"),
                ("Artiste", "Personnel"),
                ("Élodie", "L’été"),
                ("Artiste", "Visible"),
            )
        ):
            entry = ensure_entry(db, artist, title)
            entry.primary_genre = "Synthetic"
            for station_id in (1, 2):
                at = now - timedelta(hours=1, minutes=index)
                db.add(
                    Play(
                        station_id=station_id,
                        artist=artist,
                        title=title,
                        raw_metadata="fixture " * 1024,
                        source="json",
                        detected_at=at,
                        last_seen_at=at,
                    )
                )
        artist, title = normalized_pair("Élodie", "L’été")
        db.add(
            GlobalExclusion(
                id=exclusion_id("Élodie", "L’été"),
                artist="Élodie",
                title="L’été",
                artist_normalized=artist,
                title_normalized=title,
            )
        )
        db.commit()
    expected = snapshot(engine, now)
    migrate(True)
    names = {
        index["name"]
        for table in ("plays", "track_metadata")
        for index in inspect(engine).get_indexes(table)
    }
    assert INDEX_NAMES <= names
    with engine.connect() as conn:
        assert INDEX_NAMES <= set(conn.scalars(text("SELECT idx FROM sqlite_stat1")))
    assert snapshot(engine, now) == expected
    migrate(False)
    names = {
        index["name"]
        for table in ("plays", "track_metadata")
        for index in inspect(engine).get_indexes(table)
    }
    assert not INDEX_NAMES & names
    assert snapshot(engine, now) == expected
    migrate(True)
    assert snapshot(engine, now) == expected
    engine.dispose()


def test_sqlite_specific_indexes_do_not_change_postgresql_schema():
    statements = []
    engine = create_mock_engine(
        "postgresql://",
        lambda sql, *args, **kwargs: statements.append(str(sql.compile(dialect=engine.dialect))),
    )
    Base.metadata.create_all(engine)
    assert not any(name in sql for name in INDEX_NAMES for sql in statements)
    assert any("CREATE TABLE plays" in sql for sql in statements)
