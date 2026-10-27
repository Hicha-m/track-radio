"""Read-only, duration-aware review of broadcast repetitions on every station."""

import json
import math
from datetime import datetime
from typing import Literal

from fastapi import HTTPException
from pydantic import Field, model_validator
from sqlalchemy import func, select

from app.models import Play, Station, TrackMetadata
from app.services.data_admin import play_data, utc
from app.services.exclusions import ExclusionFilters, exclusion_conditions


class AuditFilters(ExclusionFilters):
    station_id: int | None = Field(None, ge=1)
    since: datetime | None = None
    until: datetime | None = None
    margin: int = Field(15, ge=0, le=120)
    fallback: int = Field(180, ge=10, le=900)
    reason: Literal["all", "source", "duration", "consecutive", "unknown"] = "all"
    page: int = Field(1, ge=1, le=1_000_000)
    snapshot_id: int | None = Field(None, ge=0)

    @model_validator(mode="after")
    def valid_dates(self):
        if any(v is not None and v.tzinfo is None for v in (self.since, self.until)):
            raise ValueError("Indiquez un fuseau horaire pour les dates.")
        if self.since and self.until and self.since >= self.until:
            raise ValueError("La fin doit être postérieure au début.")
        return self


def source_start(raw):
    try:
        start = json.loads(raw).get("start")
        return start if type(start) is int and start > 0 else None
    except (ValueError, TypeError, AttributeError):
        return None


def track_duration(selected):
    value = selected.get("duration") if isinstance(selected, dict) else None
    return (
        float(value)
        if type(value) in (int, float) and math.isfinite(value) and 0 < value <= 86400
        else None
    )


def analyze(rows, filters):
    """Consume rows in station/track/date order; keep only one track's context."""
    stations = {}
    items = []
    previous = None
    events = {}
    group = None
    total = 0
    offset = (filters.page - 1) * 25
    for row in rows:
        key = (row.station_id, row.track_id or (row.artist, row.title))
        if key != group:
            group, previous, events = key, None, {}
        at = utc(row.detected_at)
        start = source_start(row.raw_metadata)
        duration = track_duration(row.selected)
        event = (row.source, start)
        anchor = events.get(event) if start is not None else None
        reference = anchor if anchor is not None else previous
        gap = (at - reference["at"]).total_seconds() if reference else None
        consecutive = reference is not None and row.previous_station_id == reference["id"]
        reasons = []
        if anchor is not None:
            reasons.append("source")
        if gap is not None:
            if duration is not None and gap < max(0, duration - filters.margin):
                reasons.append("duration")
            elif duration is None and gap < filters.fallback:
                reasons.append("unknown")
            if consecutive:
                reasons.append("consecutive")
        in_scope = (filters.since is None or at >= filters.since) and (
            filters.until is None or at < filters.until
        )
        if in_scope:
            summary = stations.setdefault(
                row.station_id,
                {
                    "station_id": row.station_id,
                    "station_name": row.station_name,
                    "plays": 0,
                    "with_duration": 0,
                    "suspects": 0,
                    "source": 0,
                    "duration": 0,
                    "consecutive": 0,
                    "unknown": 0,
                },
            )
            summary["plays"] += 1
            summary["with_duration"] += duration is not None
            if reasons and (filters.reason == "all" or filters.reason in reasons):
                summary["suspects"] += 1
                for reason in reasons:
                    summary[reason] += 1
                if offset <= total < offset + 25:
                    items.append(
                        {
                            "previous_id": reference["id"],
                            "current_id": row.id,
                            "reasons": reasons,
                            "gap_seconds": (at - reference["at"]).total_seconds(),
                            "duration": duration,
                            "threshold_seconds": max(0, duration - filters.margin)
                            if duration is not None
                            else filters.fallback,
                            "source_start": start,
                            "previous_source_start": reference["start"],
                        }
                    )
                total += 1
        previous = {"id": row.id, "at": at, "start": start}
        if start is not None:
            events[event] = previous
    return {
        "items": items,
        "stations": sorted(stations.values(), key=lambda s: s["station_name"].casefold()),
        "total": total,
        "page": filters.page,
        "has_more": total > offset + 25,
    }


def audit(db, filters):
    snapshot = filters.snapshot_id
    if snapshot is None:
        snapshot = db.scalar(select(func.max(Play.id))) or 0
    sqlite = db.get_bind().dialect.name == "sqlite"
    conditions = [Play.id <= snapshot, *exclusion_conditions(filters.exclusions, sqlite)]
    if filters.station_id is not None:
        conditions.append(Play.station_id == filters.station_id)
    # Keep intervening broadcasts, even hidden ones, when deciding consecutivity.
    # Only visible rows contribute to the audit and its counters.
    history = select(
        Play.id.label("play_id"),
        func.lag(Play.id)
        .over(partition_by=Play.station_id, order_by=(Play.detected_at, Play.id))
        .label("previous_station_id"),
    ).where(Play.id <= snapshot)
    if filters.station_id is not None:
        history = history.where(Play.station_id == filters.station_id)
    history = history.subquery()
    query = (
        select(
            Play.id,
            Play.station_id,
            Play.track_id,
            Play.artist,
            Play.title,
            Play.detected_at,
            Play.source,
            Play.raw_metadata,
            Station.name.label("station_name"),
            TrackMetadata.selected,
            history.c.previous_station_id,
        )
        .join(Station, Station.id == Play.station_id)
        .join(history, history.c.play_id == Play.id)
        .outerjoin(TrackMetadata, TrackMetadata.id == Play.track_id)
        .where(*conditions)
        .order_by(
            Play.station_id,
            func.coalesce(Play.track_id, func.coalesce(Play.artist, "") + "\x1f" + Play.title),
            Play.detected_at,
            Play.id,
        )
    )
    result = db.execute(query.execution_options(yield_per=250))
    try:
        report = analyze(result, filters)
    finally:
        result.close()
    ids = {item[key] for item in report["items"] for key in ("previous_id", "current_id")}
    pairs = {
        play.id: dict(play_data(play), station_name=name)
        for play, name in db.execute(
            select(Play, Station.name).join(Station).where(Play.id.in_(ids))
        )
    }
    if ids != pairs.keys():
        raise HTTPException(409, "L’historique a changé. Relancez l’analyse.")
    for item in report["items"]:
        item["previous"] = pairs[item.pop("previous_id")]
        item["current"] = pairs[item.pop("current_id")]
    report["snapshot_id"] = snapshot
    report["rules"] = {"margin": filters.margin, "fallback": filters.fallback}
    return report
