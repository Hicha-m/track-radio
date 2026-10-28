"""Bounded, cached statistics over collected detections; no provider calls."""

import json
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import monotonic
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, aliased

from app.database import get_db
from app.models import Play, Station, TrackMetadata
from app.services.exclusions import (
    ExclusionFilters,
    exclusion_conditions,
    global_revision,
)
from app.services.track_identity import identity

router = APIRouter(prefix="/api/stats")
Db = Annotated[Session, Depends(get_db)]
_cache = OrderedDict()
_lock = Lock()


class MetricsFilters(ExclusionFilters):
    station_id: int | None = Field(None, ge=1)
    hours: int = Field(12, ge=6, le=24)
    page: int = Field(1, ge=1, le=1000000)
    page_size: int = Field(25, ge=1, le=100)
    station_a: int | None = Field(None, ge=1)
    station_b: int | None = Field(None, ge=1)
    track_id: str | None = Field(None, min_length=64, max_length=64, pattern="^[a-f0-9]+$")
    timezone: str = Field("Europe/Paris", max_length=100)

    @field_validator("hours")
    @classmethod
    def allowed_hours(cls, value):
        if value not in {6, 12, 24}:
            raise ValueError("Choisir une fenêtre de 6, 12 ou 24 heures.")
        return value


def invalidate_metrics():
    with _lock:
        _cache.clear()


def epoch(column, sqlite):
    return func.unixepoch(column) if sqlite else func.extract("epoch", column)


def compute(db, kind, filters, now):
    sqlite = db.get_bind().dialect.name == "sqlite"
    hours = filters.hours if kind == "repetitions" else 168 if kind in {"overlap", "hourly"} else 24
    since = now - timedelta(hours=hours)
    base = [
        Play.detected_at >= since,
        Play.detected_at < now,
        (
            Play.track_id.is_(None)
            | Play.track_id.not_in(
                [identity(t["artist"], t["title"]) for t in json.loads(filters.exclusions or "[]")]
            )
        ),
        *exclusion_conditions(filters.exclusions, sqlite),
    ]
    if filters.station_id and kind not in {"overlap", "offsets"}:
        base.append(Play.station_id == filters.station_id)
    page = filters.page
    size = filters.page_size
    result = {"since": since, "until": now, "page": page, "page_size": size}

    def paginated(query):
        rows = db.execute(query.offset((page - 1) * size).limit(size + 1)).mappings().all()
        return {"items": [dict(r) for r in rows[:size]], "has_more": len(rows) > size}

    if kind == "summary":
        total = db.scalar(select(func.count(Play.id)).where(*base)) or 0
        distinct = select(Play.track_id).where(*base).group_by(Play.track_id).subquery()
        unique = db.scalar(select(func.count()).select_from(distinct)) or 0
        radios = db.scalar(select(func.count(func.distinct(Play.station_id))).where(*base)) or 0
        genres = compute(db, "genres", filters, now)
        top_base = [Play.detected_at >= now - timedelta(hours=filters.hours), *base[1:]]
        top = (
            db.execute(
                select(
                    Play.track_id,
                    func.min(Play.artist).label("artist"),
                    func.min(Play.title).label("title"),
                    func.count().label("plays"),
                )
                .where(*top_base)
                .group_by(Play.track_id)
                .order_by(func.count().desc(), Play.track_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        from app.services.catalog import attach_metadata

        result.update(
            total=total,
            unique_tracks=unique,
            stations=radios,
            genre_coverage=genres["coverage"],
            known_genres=sum(row["plays"] for row in genres["items"] if row["genre"] != "Inconnu"),
            top=attach_metadata(db, [dict(top)])[0] if top else None,
            top_since=now - timedelta(hours=filters.hours),
            has_more=False,
        )
    elif kind == "repetitions":
        count = func.count(Play.id)
        interval = (
            func.max(epoch(Play.detected_at, sqlite)) - func.min(epoch(Play.detected_at, sqlite))
        ) / (count - 1)
        result.update(
            paginated(
                select(
                    Play.track_id,
                    func.min(Play.artist).label("artist"),
                    func.min(Play.title).label("title"),
                    Play.station_id,
                    Station.name.label("station_name"),
                    count.label("plays"),
                    interval.label("average_seconds"),
                )
                .join(Station)
                .where(*base)
                .group_by(Play.track_id, Play.station_id, Station.name)
                .having(count >= 3)
                .order_by(count.desc(), Station.name, Play.track_id)
            )
        )
        from app.services.catalog import attach_metadata

        result["items"] = attach_metadata(db, result["items"])
    elif kind == "diversity":
        aggregate = (
            select(
                Play.station_id,
                func.count().label("plays"),
                func.count(func.distinct(Play.track_id)).label("unique_tracks"),
            )
            .where(*base)
            .group_by(Play.station_id)
            .subquery()
        )
        query = select(
            Station.id.label("station_id"),
            Station.name.label("station_name"),
            func.coalesce(aggregate.c.plays, 0).label("plays"),
            func.coalesce(aggregate.c.unique_tracks, 0).label("unique_tracks"),
        ).outerjoin(aggregate, aggregate.c.station_id == Station.id)
        if filters.station_id:
            query = query.where(Station.id == filters.station_id)
        result.update(paginated(query.order_by(Station.name)))
    elif kind == "overlap":
        tracks = (
            select(Play.station_id, Play.track_id)
            .join(Station)
            .where(*base, Station.configured.is_(True))
            .distinct()
            .cte("tracks")
        )
        other = tracks.alias("other")
        counts = dict(
            db.execute(
                select(tracks.c.station_id, func.count()).group_by(tracks.c.station_id)
            ).all()
        )
        common = {
            (a, b): n
            for a, b, n in db.execute(
                select(
                    tracks.c.station_id,
                    other.c.station_id,
                    func.count(),
                )
                .join(
                    other,
                    and_(
                        tracks.c.track_id == other.c.track_id,
                        tracks.c.station_id < other.c.station_id,
                    ),
                )
                .group_by(tracks.c.station_id, other.c.station_id)
            )
        }
        stations = [
            {"id": i, "name": n, "unique_tracks": counts.get(i, 0)}
            for i, n in db.execute(
                select(Station.id, Station.name)
                .where(Station.configured.is_(True))
                .order_by(Station.name)
            )
        ]
        pairs = []
        for a in stations:
            for b in stations:
                shared = (
                    counts.get(a["id"], 0)
                    if a["id"] == b["id"]
                    else common.get(tuple(sorted((a["id"], b["id"]))), 0)
                )
                union = a["unique_tracks"] + b["unique_tracks"] - shared
                pairs.append(
                    {
                        "station_a": a["id"],
                        "station_b": b["id"],
                        "shared": shared,
                        "union": union,
                        "percent": round(100 * shared / union, 1)
                        if a["unique_tracks"] and b["unique_tracks"]
                        else None,
                    }
                )
        result.update(stations=stations, items=pairs, has_more=False)
    elif kind == "offsets":
        if not filters.station_a or not filters.station_b or filters.station_a == filters.station_b:
            raise HTTPException(422, "Choisissez deux radios différentes.")
        other = aliased(Play)
        seconds = epoch(other.detected_at, sqlite) - epoch(Play.detected_at, sqlite)
        # Interval arithmetic differs in SQLite; epoch predicates are portable.
        query = (
            select(
                Play.id.label("id_a"),
                other.id.label("id_b"),
                Play.track_id,
                Play.artist,
                Play.title,
                Play.detected_at.label("at_a"),
                other.detected_at.label("at_b"),
                seconds.label("seconds"),
            )
            .join(
                other,
                and_(
                    Play.track_id == other.track_id,
                    other.station_id == filters.station_b,
                    other.detected_at >= since,
                    other.detected_at < now,
                    *exclusion_conditions(filters.exclusions, sqlite, other.artist, other.title),
                    func.abs(seconds) <= 300,
                ),
            )
            .where(*base, Play.station_id == filters.station_a)
            .order_by(func.abs(seconds), Play.detected_at.desc(), Play.id, other.id)
        )
        used = set()
        items = []
        matched = 0
        rows = db.execute(query.execution_options(yield_per=100)).mappings()
        try:
            for row in rows:
                if row["id_a"] in used or row["id_b"] in used:
                    continue
                used.update((row["id_a"], row["id_b"]))
                if matched >= (page - 1) * size:
                    items.append(
                        {
                            **dict(row),
                            "at_a": row["at_a"].replace(tzinfo=UTC),
                            "at_b": row["at_b"].replace(tzinfo=UTC),
                            "first_station": filters.station_a
                            if row["seconds"] > 0
                            else filters.station_b
                            if row["seconds"] < 0
                            else None,
                        }
                    )
                matched += 1
                if len(items) > size:
                    break
        finally:
            rows.close()
        result.update(items=items[:size], has_more=len(items) > size)
    elif kind == "genres":
        genre = func.coalesce(TrackMetadata.primary_genre, "Inconnu")
        rows = (
            db.execute(
                select(genre.label("genre"), func.count().label("plays"))
                .select_from(Play)
                .outerjoin(TrackMetadata, Play.track_id == TrackMetadata.id)
                .where(*base)
                .group_by(genre)
                .order_by(func.count().desc(), genre)
            )
            .mappings()
            .all()
        )
        total = sum(r["plays"] for r in rows)
        known = sum(r["plays"] for r in rows if r["genre"] != "Inconnu")
        result.update(
            items=[dict(r) for r in rows],
            total=total,
            coverage=round(100 * known / total, 1) if total else 0,
            has_more=False,
        )
    elif kind == "hourly":
        if not filters.track_id:
            raise HTTPException(422, "track_id est obligatoire.")
        try:
            zone = ZoneInfo(filters.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise HTTPException(422, "Fuseau horaire invalide.") from error
        bucket = (
            func.strftime("%Y-%m-%dT%H:00:00", Play.detected_at)
            if sqlite
            else func.date_trunc("hour", Play.detected_at)
        )
        rows = db.execute(
            select(bucket, func.count())
            .where(*base, Play.track_id == filters.track_id)
            .group_by(bucket)
        )
        buckets = [0] * 24
        for timestamp, count in rows:
            date = datetime.fromisoformat(timestamp) if isinstance(timestamp, str) else timestamp
            buckets[date.replace(tzinfo=UTC).astimezone(zone).hour] += count
        result.update(
            items=[{"hour": h, "plays": n} for h, n in enumerate(buckets)],
            timezone=filters.timezone,
            has_more=False,
        )
    return result


@router.get("/{kind}")
def metrics(
    kind: Literal["repetitions", "diversity", "overlap", "offsets", "genres", "hourly", "summary"],
    db: Db,
    filters: Annotated[MetricsFilters, Query()],
):
    key = (db.get_bind(), global_revision(db), kind, filters.model_dump_json())
    with _lock:
        cached = _cache.get(key)
        if cached and cached[0] > monotonic():
            _cache.move_to_end(key)
            return cached[1]
    result = compute(db, kind, filters, datetime.now(UTC))
    with _lock:
        _cache[key] = (monotonic() + 30, result)
        _cache.move_to_end(key)
        while len(_cache) > 64:
            _cache.popitem(last=False)
    return result
