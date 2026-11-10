"""Exact rolling windows, updated off the request path, with visibility checked at read."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import uuid4

from sqlalchemy import case, delete, func, insert, literal, or_, select, true, union_all

from app.models import Play, PlayCount, RankingCount, RankingState
from app.services.exclusions import exclusion_conditions, global_revision

PERIODS = {"1": 1, "7": 7, "30": 30, "all": None}
logger = logging.getLogger(__name__)


def raw_counts(db, since, until):
    end_hour = until.replace(minute=0, second=0, microsecond=0)
    start_hour = since.replace(minute=0, second=0, microsecond=0) if since else None
    first_full = (
        start_hour + timedelta(hours=1) if start_hour and start_hour != since else start_hour
    )
    columns = ("station_id", "track_id", "artist", "title")
    counter = select(*(getattr(PlayCount, c) for c in columns), PlayCount.plays).where(
        PlayCount.hour < end_hour,
        *exclusion_conditions("", True, artist=PlayCount.artist, title=PlayCount.title),
    )
    if first_full:
        counter = counter.where(PlayCount.hour >= first_full)
    raw = select(*(getattr(Play, c) for c in columns), func.count().label("plays")).where(
        Play.detected_at < until,
        *exclusion_conditions("", db.get_bind().dialect.name == "sqlite"),
    )
    if since:
        raw = raw.where(Play.detected_at >= since)
    if db.get_bind().dialect.name == "sqlite":
        edges = [Play.detected_at >= end_hour]
        if first_full:
            edges.append(Play.detected_at < first_full)
        raw = raw.where(or_(*edges))
        return union_all(counter, raw.group_by(*(getattr(Play, c) for c in columns))).subquery()
    return raw.group_by(*(getattr(Play, c) for c in columns)).subquery()


def cleanup(factory):
    with factory() as db:
        active = select(RankingState.generation)
        while True:
            obsolete = (
                select(RankingCount.id).where(RankingCount.generation.not_in(active)).limit(1000)
            )
            result = db.execute(delete(RankingCount).where(RankingCount.id.in_(obsolete)))
            db.commit()
            if result.rowcount == 0:
                break


def refresh(factory, now=None):
    cutoff = (now or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None, microsecond=0)
    generation = uuid4().hex
    # A single configured preparer stages short batches while collection keeps writing.
    # Publishing four state rows is atomic; a failed build leaves the last views intact.
    try:
        with factory() as source_db:
            connection = source_db.connection()
            if connection.dialect.name == "sqlite":
                raw = connection.connection.driver_connection
                if not raw.in_transaction:
                    connection.exec_driver_sql("BEGIN")
            revision = global_revision(source_db)
            for period, days in PERIODS.items():
                since = cutoff - timedelta(days=days) if days else None
                source = raw_counts(source_db, since, cutoff)
                grouped = select(
                    literal(period).label("period"),
                    literal(generation).label("generation"),
                    source.c.station_id,
                    source.c.track_id,
                    source.c.artist,
                    source.c.title,
                    func.sum(source.c.plays).label("plays"),
                ).group_by(source.c.station_id, source.c.track_id, source.c.artist, source.c.title)
                rows = source_db.execute(grouped).mappings()
                while batch := rows.fetchmany(500):
                    with factory() as writer:
                        writer.execute(insert(RankingCount), [dict(row) for row in batch])
                        writer.commit()
                # Prepare the all-stations view too; the common read need not
                # regroup a separate row for every station playing the same song.
                with factory() as writer:
                    total = (
                        select(
                            literal(period),
                            literal(generation),
                            literal(0),
                            RankingCount.track_id,
                            RankingCount.artist,
                            RankingCount.title,
                            func.sum(RankingCount.plays),
                        )
                        .where(
                            RankingCount.period == period,
                            RankingCount.generation == generation,
                            RankingCount.station_id != 0,
                        )
                        .group_by(RankingCount.track_id, RankingCount.artist, RankingCount.title)
                    )
                    writer.execute(
                        insert(RankingCount).from_select(
                            [
                                "period",
                                "generation",
                                "station_id",
                                "track_id",
                                "artist",
                                "title",
                                "plays",
                            ],
                            total,
                        )
                    )
                    writer.commit()
        with factory() as db:
            # Force a write transaction before checking the exclusions used for publication.
            db.execute(delete(RankingCount).where(RankingCount.id == -1))
            if global_revision(db) != revision:
                db.rollback()
                return
            for period in PERIODS:
                state = db.get(RankingState, period)
                if state is None:
                    state = RankingState(period=period)
                    db.add(state)
                state.calculated_at, state.exclusion_revision = cutoff, revision
                state.generation = generation
            db.commit()
    finally:
        cleanup(factory)


def read(db, period, limit, exclusions="", station_id=None, station_ids=(), now=None):
    revision = global_revision(db)
    model = RankingCount
    artist, title = func.min(model.artist).label("artist"), func.min(model.title).label("title")
    total = func.sum(model.plays).label("plays")
    grouped = (
        select(artist, title, total)
        .join(
            RankingState,
            (model.period == RankingState.period) & (model.generation == RankingState.generation),
        )
        .where(
            model.period == period,
            *exclusion_conditions(
                exclusions,
                db.get_bind().dialect.name == "sqlite",
                artist=model.artist,
                title=model.title,
            ),
        )
    )
    if not station_id and not station_ids:
        grouped = grouped.where(model.station_id == 0)
    if station_id:
        grouped = grouped.where(model.station_id == station_id)
    if station_ids:
        grouped = grouped.where(model.station_id.in_(station_ids))
    grouped = (
        grouped.group_by(
            case((model.track_id.is_(None), model.artist)),
            case((model.track_id.is_(None), model.title)),
            model.track_id,
        )
        .order_by(total.desc(), title, artist, model.track_id)
        .limit(limit)
        .subquery()
    )
    rows = db.execute(
        select(
            RankingState.calculated_at,
            grouped.c.artist,
            grouped.c.title,
            grouped.c.plays,
        )
        .select_from(RankingState)
        .outerjoin(grouped, true())
        .where(
            RankingState.period == period,
            RankingState.exclusion_revision == revision,
        )
    ).all()
    if not rows or global_revision(db) != revision:
        return None
    cutoff = rows[0].calculated_at.replace(tzinfo=UTC)
    days = PERIODS[period]
    return {
        "calculated_at": cutoff,
        "since": cutoff - timedelta(days=days) if days else None,
        "until": cutoff,
        "stale": ((now or datetime.now(UTC)) - cutoff).total_seconds() > 60,
        "items": [
            {"artist": r.artist, "title": r.title, "plays": r.plays}
            for r in rows
            if r.plays is not None
        ],
    }


async def loop(factory):
    from sqlalchemy.orm import sessionmaker

    from app.database import make_engine

    # Own pools keep the preparer's read snapshot and brief writes away from
    # the collector's two connections.
    with factory() as db:
        ranking_engine = make_engine(db.get_bind().url.render_as_string(hide_password=False))
    ranking_factory = sessionmaker(bind=ranking_engine, expire_on_commit=False)
    while True:
        started = monotonic()
        try:
            await asyncio.to_thread(refresh, ranking_factory)
        except Exception:
            logger.warning("Préparation du classement indisponible ; nouvel essai dans 30 s")
        await asyncio.sleep(max(1, 30 - (monotonic() - started)))
