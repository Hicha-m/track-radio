"""Worker-side technical evidence. No URLs, track labels, raw bodies or exceptions."""

import asyncio
import json
import logging
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from uuid import uuid4

import httpx
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.models import CollectionEvent, CollectionStatus, Station
from app.services.collection_schedule import failure_delay, retry_after

logger = logging.getLogger("collector")
round_context = ContextVar("collection_round", default=None)
retry_context = ContextVar("collection_failures", default=0)
trace_context = ContextVar("collection_trace", default=None)
SUCCESS = {"new", "unchanged", "excluded", "stale", "disabled", "non_music"}
OUTCOMES = SUCCESS | {
    "queued",
    "running",
    "http_error",
    "timeout",
    "network_error",
    "format_error",
    "empty",
    "internal_error",
    "interrupted",
    "probe_ok",
}
PROBE_SOURCES = ("icy", "skyrock_super", "skyrock_onair")
RETENTION_HOURS = 48
STATION_EVENT_LIMIT = 500
GLOBAL_EVENT_LIMIT = 5000
PROBE_COOLDOWN_SECONDS = 300


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass
class Trace:
    event_id: int | None = None
    started: float = field(default_factory=time.monotonic)
    responses: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def observe_http(response):
    """Called before raise_for_status/extraction, in the current attempt's context."""
    trace = trace_context.get()
    if trace is None or len(trace.responses) >= 6:
        return
    item = {"status": response.status_code}
    if response.status_code in {429, 503}:
        delay = retry_after(response.headers.get("retry-after"))
        if delay is not None:
            trace.summary["retry_after_seconds"] = delay
    for header in ("date", "last-modified"):
        try:
            value = parsedate_to_datetime(response.headers.get(header, "")[:128])
            if value.tzinfo:
                item[header] = value.astimezone(UTC).isoformat()
        except (TypeError, ValueError, IndexError, OverflowError):
            pass
    age = response.headers.get("age", "")
    if age.isascii() and age.isdigit() and len(age) <= 8:
        item["age"] = int(age)
    control = response.headers.get("cache-control", "").lower()
    item["cache"] = [
        v for v in ("no-store", "no-cache", "private", "public") if v in control.split(", ")
    ]
    trace.responses.append(item)


def error_outcome(error):
    if isinstance(error, httpx.HTTPStatusError):
        return "http_error"
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(error, (httpx.RequestError, OSError)) or (
        isinstance(error, ValueError) and str(error) == "Le nom de domaine ne peut pas être résolu."
    ):
        return "network_error"
    if isinstance(
        error, (ValueError, KeyError, IndexError, TypeError, AttributeError, OverflowError)
    ):
        return "format_error"
    return "internal_error"


def safe_persist(function, *args, **kwargs):
    # Diagnostics must not mask an upstream exception or prevent a valid insertion.
    try:
        return function(*args, **kwargs)
    except Exception:
        logger.error('collection_diagnostic {"outcome":"storage_error"}')
        return None


def station_status(db, station_id):
    status = db.get(CollectionStatus, station_id)
    if status is None:
        try:
            with db.begin_nested():
                db.add(CollectionStatus(station_id=station_id))
                db.flush()
        except IntegrityError:
            pass  # The API and collector can first encounter the station together.
        status = db.get(CollectionStatus, station_id)
    return status


def begin(factory, station_id, source):
    now = datetime.now(UTC)
    with factory() as db:
        if db.get(Station, station_id) is None:
            return None
        status = station_status(db, station_id)
        abandoned = db.execute(
            update(CollectionEvent)
            .where(
                CollectionEvent.station_id == station_id,
                CollectionEvent.kind == "collection",
                CollectionEvent.outcome == "running",
            )
            .values(outcome="interrupted", finished_at=now)
        ).rowcount
        status.errors += abandoned
        status.consecutive_errors += abandoned
        status.last_attempt_at = now
        status.runtime = {
            "poll_interval_seconds": settings.poll_interval_seconds,
            "request_timeout_seconds": settings.request_timeout_seconds,
            "collector_concurrency": settings.collector_concurrency,
            "max_stations": settings.max_stations,
            "last_seen_write_seconds": settings.last_seen_write_seconds,
            "revision": os.getenv("APP_REVISION", "development")[:40],
        }
        status.next_attempt_at = None
        status.outcome = "running"
        status.attempts += 1
        event = CollectionEvent(
            attempt_id=uuid4().hex,
            round_id=round_context.get(),
            station_id=station_id,
            source=source,
            kind="collection",
            started_at=now,
            outcome="running",
        )
        db.add(event)
        db.commit()
        logger.info(
            "collection_diagnostic %s",
            json.dumps(
                {
                    "worker": "collector",
                    "station_id": station_id,
                    "source": source,
                    "attempt_id": event.attempt_id,
                    "round_id": event.round_id,
                    "outcome": "running",
                    "started_at": now.isoformat(),
                }
            ),
        )
        return event.id


def finish(factory, trace, outcome, *, changed=False):
    if trace.event_id is None:
        return
    if outcome not in OUTCOMES:
        raise ValueError("Unknown collection outcome")
    now = datetime.now(UTC)
    with factory() as db:
        event = db.get(CollectionEvent, trace.event_id)
        if event is None:
            return
        event.outcome = outcome
        event.finished_at = now
        event.duration_ms = max(0, round((time.monotonic() - trace.started) * 1000))
        event.http_status = trace.responses[-1]["status"] if trace.responses else None
        event.details = {"http": trace.responses, **trace.summary}
        if event.kind == "probe" and trace.summary.get("retry_after_seconds") is not None:
            event.next_attempt_at = now + timedelta(seconds=trace.summary["retry_after_seconds"])
            status = db.get(CollectionStatus, event.station_id)
            if status and (
                not status.next_attempt_at or utc(status.next_attempt_at) < event.next_attempt_at
            ):
                status.next_attempt_at = event.next_attempt_at
        if event.kind == "collection":
            status = db.get(CollectionStatus, event.station_id)
            status.outcome = outcome
            status.http_status = event.http_status
            status.duration_ms = event.duration_ms
            if outcome in SUCCESS:
                status.successes += 1
                status.consecutive_errors = 0
                status.last_success_at = now
                if outcome not in {"stale", "disabled"}:
                    status.last_observation_at = now
                if changed:
                    status.last_change_at = now
                delay = max(0, settings.poll_interval_seconds - (time.monotonic() - trace.started))
            else:
                status.errors += 1
                status.consecutive_errors += 1
                delay = failure_delay(
                    retry_context.get() + 1, trace.summary.get("retry_after_seconds")
                )
            event.next_attempt_at = status.next_attempt_at = now + timedelta(seconds=delay)
        db.commit()
        logger.info(
            "collection_diagnostic %s",
            json.dumps(
                {
                    "worker": "collector",
                    "station_id": event.station_id,
                    "source": event.source,
                    "kind": event.kind,
                    "attempt_id": event.attempt_id,
                    "round_id": event.round_id,
                    "outcome": outcome,
                    "duration_ms": event.duration_ms,
                    "http_status": event.http_status,
                    "finished_at": now.isoformat(),
                }
            ),
        )


def prune(factory):
    with factory() as db:
        db.execute(
            delete(CollectionEvent).where(
                CollectionEvent.started_at < datetime.now(UTC) - timedelta(hours=RETENTION_HOURS)
            )
        )
        for station_id in db.scalars(select(CollectionEvent.station_id).distinct()):
            cutoff = db.scalar(
                select(CollectionEvent.id)
                .where(
                    CollectionEvent.station_id == station_id,
                )
                .order_by(CollectionEvent.id.desc())
                .offset(STATION_EVENT_LIMIT - 1)
                .limit(1)
            )
            if cutoff:
                db.execute(
                    delete(CollectionEvent).where(
                        CollectionEvent.station_id == station_id,
                        CollectionEvent.id < cutoff,
                    )
                )
        cutoff = db.scalar(
            select(CollectionEvent.id)
            .order_by(CollectionEvent.id.desc())
            .offset(GLOBAL_EVENT_LIMIT - 1)
            .limit(1)
        )
        if cutoff:
            db.execute(delete(CollectionEvent).where(CollectionEvent.id < cutoff))
        db.commit()


def probe_available(station):
    # Fixed destinations only. No user-controlled endpoint or credentials accepted.
    return station.slug == "skyrock" and station.stream_url.rstrip("/") == (
        "https://icecast.skyrock.net/s/natio_mp3_128k"
    )


def enqueue_probes(db, station):
    from fastapi import HTTPException

    if not station.enabled or not probe_available(station):
        raise HTTPException(422, "Sondes disponibles pour le flux national Skyrock actif.")
    now = datetime.now(UTC)
    station_status(db, station.id)
    claimed = db.execute(
        update(CollectionStatus)
        .where(
            CollectionStatus.station_id == station.id,
            or_(
                CollectionStatus.probe_requested_at.is_(None),
                CollectionStatus.probe_requested_at
                <= (now - timedelta(seconds=PROBE_COOLDOWN_SECONDS)),
            ),
        )
        .values(probe_requested_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    pending = db.scalar(
        select(CollectionEvent.id)
        .where(
            CollectionEvent.station_id == station.id,
            CollectionEvent.kind == "probe",
            CollectionEvent.outcome.in_(["queued", "running"]),
        )
        .limit(1)
    )
    if not claimed or pending:
        db.rollback()
        raise HTTPException(
            429,
            "Sondes déjà demandées ; attendre leur fin et cinq minutes.",
            headers={"Retry-After": str(PROBE_COOLDOWN_SECONDS)},
        )
    for source in PROBE_SOURCES:
        db.add(
            CollectionEvent(
                attempt_id=uuid4().hex,
                station_id=station.id,
                source=source,
                kind="probe",
                started_at=now,
                outcome="queued",
            )
        )
    db.commit()
    return {"queued": len(PROBE_SOURCES)}


def claim_probe(factory, round_id):
    now = datetime.now(UTC)
    with factory() as db:
        # A worker restart must not leave a claimed probe pending forever.
        db.execute(
            update(CollectionEvent)
            .where(
                CollectionEvent.kind == "probe",
                CollectionEvent.outcome == "running",
                CollectionEvent.started_at < now - timedelta(seconds=120),
            )
            .values(outcome="interrupted", finished_at=now)
            .execution_options(synchronize_session=False)
        )
        event = db.scalar(
            select(CollectionEvent)
            .where(
                CollectionEvent.kind == "probe",
                CollectionEvent.outcome == "queued",
            )
            .order_by(CollectionEvent.id)
            .limit(1)
        )
        if event is None:
            db.commit()
            return None
        station = db.get(Station, event.station_id)
        cooldown = db.scalar(
            select(CollectionEvent.id)
            .where(
                CollectionEvent.station_id == event.station_id,
                CollectionEvent.http_status.in_([429, 503]),
                CollectionEvent.next_attempt_at > now,
            )
            .limit(1)
        )
        if cooldown:
            db.commit()
            return None
        if (
            not station
            or not station.enabled
            or not probe_available(station)
            or event.source not in PROBE_SOURCES
        ):
            event.outcome = "disabled"
            event.finished_at = now
            db.commit()
            return None
        claimed = db.execute(
            update(CollectionEvent)
            .where(
                CollectionEvent.id == event.id,
                CollectionEvent.outcome == "queued",
            )
            .values(outcome="running", started_at=now, round_id=round_id)
        ).rowcount
        db.commit()
        return (event.id, event.source, station.stream_url) if claimed else None


def probe_summary(payload, source):
    if not isinstance(payload, dict) or type(payload.get("ts")) is not int:
        raise ValueError("Invalid provider clock")
    provider_at = datetime.fromtimestamp(payload["ts"], UTC)
    schedule = (
        payload.get("radios", {}).get("natio", {}).get("schedule")
        if (source == "skyrock_super")
        else payload.get("schedule")
    )
    if not isinstance(schedule, list) or len(schedule) > 100:
        raise ValueError("Invalid provider schedule")
    active = 0
    ended = []
    for event in schedule:
        info = event.get("info", {})
        start, end = int(info["start_ts"]), int(info["end_ts"])
        if start >= end:
            raise ValueError("Invalid provider period")
        active += start <= payload["ts"] < end
        if end <= payload["ts"]:
            ended.append(end)
    return {
        "provider_at": provider_at.isoformat(),
        "schedule_entries": len(schedule),
        "active_entries": active,
        "latest_ended_at": datetime.fromtimestamp(max(ended), UTC).isoformat() if ended else None,
    }


async def process_probe(factory, client, round_id):
    from app.services.metadata import fetch_metadata, read_bounded

    claimed = await asyncio.to_thread(safe_persist, claim_probe, factory, round_id)
    if not claimed:
        return
    event_id, source, stream_url = claimed
    trace = Trace(event_id=event_id)
    token = trace_context.set(trace)
    outcome = "probe_ok"
    try:
        async with asyncio.timeout(settings.request_timeout_seconds):
            if source == "icy":
                metadata = await fetch_metadata(
                    Station(stream_url=stream_url, source_type="icy"),
                    settings.request_timeout_seconds,
                    client,
                )
                if metadata is None:
                    outcome = "empty"
            else:
                endpoint = "/super" if source == "skyrock_super" else ""
                content = await read_bounded(
                    client, "https://skyrock.fm/api/v3/player/onair" + endpoint
                )
                if not content.strip():
                    outcome = "empty"
                else:
                    trace.summary = probe_summary(json.loads(content), source)
                    if not trace.summary["schedule_entries"]:
                        outcome = "empty"
    except Exception as error:
        outcome = error_outcome(error)
    finally:
        await asyncio.to_thread(safe_persist, finish, factory, trace, outcome)
        trace_context.reset(token)
