"""Bounded admin reads of technical evidence, independent of musical visibility."""

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import Field, field_validator
from sqlalchemy import select

from app.config import settings
from app.models import CollectionEvent, CollectionStatus, Station
from app.services.collection_diagnostics import (
    GLOBAL_EVENT_LIMIT,
    OUTCOMES,
    PROBE_COOLDOWN_SECONDS,
    RETENTION_HOURS,
    STATION_EVENT_LIMIT,
    probe_available,
    utc,
)
from app.services.exclusions import ExclusionFilters


class CollectionFilters(ExclusionFilters):
    station_id: int | None = Field(None, ge=1, le=2**63 - 1)
    hours: Literal[1, 6, 24, 48] = 1
    outcome: str = Field("", max_length=32)
    kind: Literal["all", "collection", "probe"] = "all"
    before_id: int | None = Field(None, ge=1, le=2**63 - 1)
    limit: int = Field(50, ge=1, le=100)

    @field_validator("hours", mode="before")
    @classmethod
    def parse_hours(cls, value):
        return (
            int(value) if isinstance(value, str) and value.isascii() and value.isdigit() else value
        )

    @field_validator("outcome")
    @classmethod
    def known_outcome(cls, value):
        if value and value not in OUTCOMES:
            raise ValueError("Unknown collection outcome")
        return value


def age(value, now):
    return max(0, round((now - utc(value)).total_seconds())) if value else None


def public_details(details):
    """Explicit output allowlist, including for imported or older records."""
    result = {}
    if not isinstance(details, dict):
        return result
    for name in ("provider_at", "latest_ended_at", "source_started_at"):
        try:
            value = datetime.fromisoformat(details[name])
            if value.tzinfo:
                result[name] = utc(value).isoformat()
        except (KeyError, ValueError, TypeError):
            pass
    for name in ("schedule_entries", "active_entries"):
        value = details.get(name)
        if type(value) is int and 0 <= value <= 100:
            result[name] = value
    result["http"] = []
    responses = details.get("http")
    for item in (responses if isinstance(responses, list) else [])[:6]:
        if not isinstance(item, dict):
            continue
        clean = {}
        if type(item.get("status")) is int and 100 <= item["status"] <= 599:
            clean["status"] = item["status"]
        for name in ("date", "last-modified"):
            try:
                value = datetime.fromisoformat(item[name])
                if value.tzinfo:
                    clean[name] = utc(value).isoformat()
            except (KeyError, ValueError, TypeError):
                pass
        if type(item.get("age")) is int and 0 <= item["age"] <= 99999999:
            clean["age"] = item["age"]
        cache = item.get("cache")
        clean["cache"] = [
            v
            for v in ("no-cache", "no-store", "private", "public")
            if v in (cache if isinstance(cache, list) else [])
        ]
        result["http"].append(clean)
    return result


def overview(db, filters, now=None):
    now = now or datetime.now(UTC)
    observation_threshold = max(
        180, 3 * settings.poll_interval_seconds + int(settings.request_timeout_seconds)
    )
    change_threshold = 900
    query = (
        select(Station, CollectionStatus)
        .outerjoin(CollectionStatus, Station.id == CollectionStatus.station_id)
        .order_by(Station.name, Station.id)
    )
    if filters.station_id is not None:
        query = query.where(Station.id == filters.station_id)
    stations = []
    for station, status in db.execute(query.limit(100)):
        observation_age = age(status.last_observation_at, now) if status else None
        change_age = age(status.last_change_at, now) if status else None
        health = "unknown"
        if not station.enabled:
            health = "disabled"
        elif status and status.outcome == "running":
            health = (
                "overdue"
                if age(status.last_attempt_at, now) > (settings.request_timeout_seconds + 30)
                else "running"
            )
        elif status and status.consecutive_errors:
            health = "error"
        elif observation_age is not None:
            health = (
                "observation_old"
                if observation_age > observation_threshold
                else (
                    "unchanged"
                    if change_age is not None and change_age > change_threshold
                    else "observed"
                )
            )
        item = {
            "station_id": station.id,
            "name": station.name,
            "source": station.source_type,
            "enabled": station.enabled,
            "health": health,
            "observation_age_seconds": observation_age,
            "change_age_seconds": change_age,
            "probe_available": station.enabled and probe_available(station),
        }
        for key in (
            "last_attempt_at",
            "last_success_at",
            "last_observation_at",
            "last_change_at",
            "next_attempt_at",
            "probe_requested_at",
            "outcome",
            "http_status",
            "duration_ms",
            "attempts",
            "successes",
            "errors",
            "consecutive_errors",
        ):
            value = getattr(status, key) if status else None
            item[key] = utc(value) if isinstance(value, datetime) else value
        runtime = status.runtime if status and isinstance(status.runtime, dict) else {}
        item["runtime"] = {
            key: value
            for key, value in runtime.items()
            if key
            in {
                "poll_interval_seconds",
                "request_timeout_seconds",
                "collector_concurrency",
                "max_stations",
                "last_seen_write_seconds",
            }
            and type(value) in (int, float)
            and 0 < value <= 3600
        }
        revision = runtime.get("revision")
        item["worker_revision"] = (
            revision
            if (
                isinstance(revision, str)
                and len(revision) == 40
                and all(v in "0123456789abcdef" for v in revision)
            )
            else None
        )
        stations.append(item)
    query = (
        select(CollectionEvent, Station.name)
        .join(Station)
        .where(CollectionEvent.started_at >= now - timedelta(hours=filters.hours))
    )
    if filters.station_id is not None:
        query = query.where(CollectionEvent.station_id == filters.station_id)
    if filters.outcome:
        query = query.where(CollectionEvent.outcome == filters.outcome)
    if filters.kind != "all":
        query = query.where(CollectionEvent.kind == filters.kind)
    if filters.before_id:
        query = query.where(CollectionEvent.id < filters.before_id)
    rows = db.execute(query.order_by(CollectionEvent.id.desc()).limit(filters.limit + 1)).all()
    events = []
    for event, station_name in rows[: filters.limit]:
        item = {
            key: getattr(event, key)
            for key in (
                "id",
                "attempt_id",
                "round_id",
                "station_id",
                "kind",
                "source",
                "started_at",
                "finished_at",
                "outcome",
                "duration_ms",
                "http_status",
                "next_attempt_at",
            )
        }
        item.update(
            worker="collector", station_name=station_name, details=public_details(event.details)
        )
        for key, value in item.items():
            if isinstance(value, datetime):
                item[key] = utc(value)
        events.append(item)
    return {
        "checked_at": now,
        "items": stations,
        "events": events,
        "next_before_id": events[-1]["id"] if len(rows) > filters.limit else None,
        "thresholds": {
            "observation_seconds": observation_threshold,
            "unchanged_seconds": change_threshold,
            "retention_hours": RETENTION_HOURS,
            "station_event_limit": STATION_EVENT_LIMIT,
            "global_event_limit": GLOBAL_EVENT_LIMIT,
            "probe_cooldown_seconds": PROBE_COOLDOWN_SECONDS,
        },
    }
