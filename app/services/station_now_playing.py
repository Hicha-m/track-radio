"""Read bounded current observations without contacting station or music providers."""

import json
import math
from datetime import UTC, datetime, timedelta

from pydantic import Field
from sqlalchemy import and_, func, select, true

from app.config import settings
from app.models import GlobalExclusion, Play, Station, TrackMetadata
from app.services.catalog import safe_url
from app.services.exclusions import ExclusionFilters, exclusion_conditions, normalized_pair
from app.services.track_identity import identity


class StationNowFilters(ExclusionFilters):
    station_id: int | None = Field(None, ge=1, le=2**63 - 1)


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def now_playing(db, exclusions="", now=None, station_id=None):
    now = utc(now or datetime.now(UTC))
    fresh_seconds = max(
        180,
        settings.last_seen_write_seconds
        + 2 * settings.poll_interval_seconds
        + math.ceil(settings.request_timeout_seconds),
    )
    # Select the actual latest row first, never the latest row surviving the exclusions.
    latest = (
        select(Play.id)
        .where(Play.station_id == Station.id)
        .order_by(Play.detected_at.desc(), Play.id.desc())
        .limit(1)
        .correlate(Station)
        .scalar_subquery()
    )
    query = select(Station.id, Station.enabled, Station.collection_state, latest.label("latest_id"))
    if station_id is not None:
        query = query.where(Station.id == station_id)
    stations = db.execute(query.order_by(Station.name, Station.id)).all()
    selected = {}
    for station in stations:
        state = station.collection_state
        # A blocked transition has no play_id. Do not resurrect the preceding song.
        selected[station.id] = (
            state.get("play_id")
            if isinstance(state, dict) and "play_id" in state
            else station.latest_id
        )
    ids = {value for value in selected.values() if type(value) is int and value > 0}
    # SQL predicates below also apply to authenticated admins.
    predicates = exclusion_conditions(exclusions, db.get_bind().dialect.name == "sqlite")
    visible = and_(*predicates)
    rows = (
        {
            row.id: row
            for row in db.execute(
                select(
                    Play.id,
                    Play.station_id,
                    Play.track_id,
                    Play.artist,
                    Play.title,
                    Play.detected_at,
                    Play.last_seen_at,
                    visible.label("visible"),
                ).where(Play.id.in_(ids))
            )
        }
        if ids
        else {}
    )
    hidden = set(
        db.execute(
            select(GlobalExclusion.artist_normalized, GlobalExclusion.title_normalized)
        ).all()
    )
    hidden.update(
        normalized_pair(t.get("artist"), t["title"]) for t in json.loads(exclusions or "[]")
    )
    # A hidden observation keeps the last visible song, explicitly marked last-known.
    fallback_stations = set()
    for station in stations:
        state = station.collection_state if isinstance(station.collection_state, dict) else {}
        row = rows.get(selected[station.id])
        if (
            row is not None
            and row.station_id == station.id
            and (not row.visible or normalized_pair(row.artist, row.title) in hidden)
        ):
            fallback_stations.add(station.id)
        elif (
            selected[station.id] is None
            and state.get("title")
            and normalized_pair(state.get("artist"), state["title"]) in hidden
        ):
            fallback_stations.add(station.id)
    if fallback_stations:
        ranked = (
            select(
                Play.id,
                func.row_number()
                .over(
                    partition_by=Play.station_id,
                    order_by=(Play.detected_at.desc(), Play.id.desc()),
                )
                .label("position"),
            )
            .where(
                Play.station_id.in_(fallback_stations),
                *predicates,
                Play.detected_at <= now,
                Play.last_seen_at <= now,
            )
            .subquery()
        )
        previous = db.execute(
            select(
                Play.id,
                Play.station_id,
                Play.track_id,
                Play.artist,
                Play.title,
                Play.detected_at,
                Play.last_seen_at,
                true().label("visible"),
            )
            .join(ranked, ranked.c.id == Play.id)
            .where(ranked.c.position == 1)
        ).all()
        for row in previous:
            rows[row.id] = row
            selected[row.station_id] = row.id
    keys = {row.track_id or identity(row.artist, row.title) for row in rows.values() if row.visible}
    metadata = (
        {
            row.id: {
                **(row.selected or {}),
                "cover_url": safe_url((row.selected or {}).get("cover_url"))
                or safe_url(row.temporary_cover_url),
            }
            for row in db.execute(
                select(
                    TrackMetadata.id, TrackMetadata.selected, TrackMetadata.temporary_cover_url
                ).where(TrackMetadata.id.in_(keys))
            )
        }
        if keys
        else {}
    )
    items = []
    for station in stations:
        item = {
            "station_id": station.id,
            "status": "unavailable",
            "observed_at": None,
            "fresh_until": None,
            "track": None,
        }
        state = station.collection_state if isinstance(station.collection_state, dict) else {}
        play_id = selected[station.id]
        if play_id is None and state.get("title"):
            if normalized_pair(state.get("artist"), state["title"]) in hidden:
                item["status"] = "hidden"
            items.append(item)
            continue
        row = rows.get(play_id) if type(play_id) is int else None
        if row is None or row.station_id != station.id:
            items.append(item)
            continue
        if not row.visible or normalized_pair(row.artist, row.title) in hidden:
            item["status"] = "hidden"
            items.append(item)
            continue
        observed = utc(row.last_seen_at)
        detected = utc(row.detected_at)
        if observed > now or detected > now:
            items.append(item)
            continue
        key = row.track_id or identity(row.artist, row.title)
        info = metadata.get(key) or {}
        duration = info.get("duration")
        event_window = (
            max(300, min(7200, duration + fresh_seconds))
            if type(duration) in (int, float) and math.isfinite(duration) and duration > 0
            else 900
        )
        started = detected
        # Source event time can reveal a stale cached JSON/HTML response despite recent polling.
        source_start = state.get("start") if station.id not in fallback_stations else None
        if type(source_start) is int and source_start > 0:
            try:
                started = datetime.fromtimestamp(source_start / 1000, UTC)
            except (OverflowError, OSError, ValueError):
                started = detected - timedelta(days=1)
        expires = min(
            observed + timedelta(seconds=fresh_seconds),
            started + timedelta(seconds=event_window),
        )
        current = (
            station.id not in fallback_stations
            and station.enabled
            and started <= now
            and now <= expires
        )
        item.update(
            status="current" if current else "last_known",
            observed_at=observed,
            fresh_until=expires if current else None,
            track={
                "artist": row.artist,
                "title": row.title,
                "metadata": {"track_id": key, "cover_url": safe_url(info.get("cover_url"))},
            },
        )
        items.append(item)
    return {"checked_at": now, "fresh_for_seconds": fresh_seconds, "items": items}
