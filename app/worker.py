import asyncio
import logging
import time
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import CollectionStatus, Station
from app.services import collection_diagnostics as diagnostics
from app.services.catalog import enrichment_loop, ensure_entry
from app.services.collection_schedule import failure_delay, next_delay, retry_after
from app.services.collector import record_metadata
from app.services.maintenance import archive_old_plays
from app.services.metadata import fetch_metadata
from app.services.network import metadata_client
from app.services.supervision import report


def restore_schedule():
    from datetime import UTC, datetime

    now, monotonic = datetime.now(UTC), time.monotonic()
    retries, schedule = {}, {}
    with SessionLocal() as db:
        for status in db.scalars(
            select(CollectionStatus).join(Station).where(Station.enabled.is_(True))
        ):
            if status.next_attempt_at:
                remaining = (diagnostics.utc(status.next_attempt_at) - now).total_seconds()
                if remaining > 0:
                    schedule[status.station_id] = monotonic + remaining
                if status.consecutive_errors:
                    retries[status.station_id] = (
                        status.consecutive_errors,
                        monotonic + max(0, remaining),
                    )
    return retries, schedule


logging.basicConfig(level=settings.log_level)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("collector")


def load_stations():
    with SessionLocal() as db:
        stations = list(
            db.scalars(
                select(Station)
                .where(Station.enabled.is_(True))
                .order_by(Station.id)
                .limit(settings.max_stations)
            )
        )
        db.expunge_all()
        return stations


def save_metadata(station_id, metadata, *, with_decision=False):
    with SessionLocal() as db:
        station = db.get(Station, station_id)
        if not station or not station.enabled:
            return {"code": "disabled", "changed": False} if with_decision else False
        decision = {}
        play, created = record_metadata(db, station, metadata, decision=decision)
        if play is not None:
            entry = ensure_entry(db, play.artist, play.title)
            if metadata.cover_url and decision.get("code") in {"new", "unchanged"}:
                from app.services.catalog import safe_url

                entry.temporary_cover_url = safe_url(metadata.cover_url)
        if created and play is not None and settings.youtube_api_key.get_secret_value():
            from app.services.youtube import queue

            queue(db, play.track_id, play.artist, play.title)
        db.commit()
        return decision if with_decision else created


def needs_artwork(metadata):
    from app.models import GlobalExclusion, TrackMetadata
    from app.services.catalog import safe_url
    from app.services.exclusions import exclusion_id
    from app.services.track_identity import identity

    if not metadata.is_music:
        return False
    with SessionLocal() as db:
        if db.get(GlobalExclusion, exclusion_id(metadata.artist, metadata.title)) is not None:
            return False
        entry = db.get(TrackMetadata, identity(metadata.artist, metadata.title))
        return entry is None or not (
            safe_url((entry.selected or {}).get("cover_url")) or safe_url(entry.temporary_cover_url)
        )


async def collect_station(station: Station, client: httpx.AsyncClient):
    # Detached snapshot: no database connection is held during network I/O.
    event_id = await asyncio.to_thread(
        diagnostics.safe_persist, diagnostics.begin, SessionLocal, station.id, station.source_type
    )
    trace = diagnostics.Trace(event_id=event_id)
    token = diagnostics.trace_context.set(trace)
    outcome, changed = "internal_error", False
    try:
        metadata = await fetch_metadata(station, settings.request_timeout_seconds, client)
        if metadata is None:
            outcome = "empty"
            return False
        from dataclasses import replace

        from app.services.temporary_artwork import cover_for

        if await asyncio.to_thread(needs_artwork, metadata):
            cover = await cover_for(station, metadata, client)
            if cover:
                metadata = replace(metadata, cover_url=cover)
        decision = await asyncio.to_thread(save_metadata, station.id, metadata, with_decision=True)
        outcome, changed = decision["code"], decision["changed"]
        if metadata.source_started_ms is not None:
            from datetime import UTC, datetime

            try:
                trace.summary["source_started_at"] = datetime.fromtimestamp(
                    metadata.source_started_ms / 1000, UTC
                ).isoformat()
            except (ValueError, TypeError, OverflowError, OSError):
                pass  # An unrepresentable diagnostic date must not change collection behavior.
        return True
    except Exception as error:
        outcome = diagnostics.error_outcome(error)
        raise
    finally:
        await asyncio.to_thread(
            diagnostics.safe_persist,
            diagnostics.finish,
            SessionLocal,
            trace,
            outcome,
            changed=changed,
        )
        diagnostics.trace_context.reset(token)


async def collect_round(client, retry_state, schedule=None):
    round_id = uuid4().hex
    if schedule is not None:
        _, persisted = await asyncio.to_thread(restore_schedule)
        for station_id, due in persisted.items():
            schedule[station_id] = max(schedule.get(station_id, 0), due)
    stations = await asyncio.to_thread(load_stations)
    active = {station.id for station in stations}
    if schedule is not None:
        for station_id in set(schedule) - active:
            schedule.pop(station_id)
    for station_id in set(retry_state) - active:
        retry_state.pop(station_id)
    queue = asyncio.Queue()
    for station in stations:
        if retry_state.get(station.id, (0, 0))[1] <= time.monotonic() and (
            schedule is None or schedule.get(station.id, 0) <= time.monotonic()
        ):
            queue.put_nowait(station)

    total = queue.qsize()
    completed = failed = 0
    await asyncio.to_thread(
        report, SessionLocal, "collector", "started", total=total, completed=0, failed=0
    )

    async def consume():
        nonlocal completed, failed
        while not queue.empty():
            station = queue.get_nowait()
            started = time.monotonic()
            provider_delay = None
            round_token = diagnostics.round_context.set(round_id)
            retry_token = diagnostics.retry_context.set(retry_state.get(station.id, (0, 0))[0])
            try:
                ok = await collect_station(station, client)
                if ok:
                    if schedule is not None:
                        schedule[station.id] = started + settings.poll_interval_seconds
                    retry_state.pop(station.id, None)
                    completed += 1
                    await asyncio.to_thread(
                        report, SessionLocal, "collector", "metadata", completed=completed
                    )
                    continue
                reason = "aucune métadonnée"
            except Exception as error:
                reason = type(error).__name__
                if isinstance(error, httpx.HTTPStatusError) and error.response.status_code in {
                    429,
                    503,
                }:
                    provider_delay = retry_after(error.response.headers.get("retry-after"))
            finally:
                diagnostics.round_context.reset(round_token)
                diagnostics.retry_context.reset(retry_token)
            failed += 1
            await asyncio.to_thread(report, SessionLocal, "collector", "metadata", failed=failed)
            failures = retry_state.get(station.id, (0, 0))[0] + 1
            delay = failure_delay(failures, provider_delay)
            retry_state[station.id] = (failures, time.monotonic() + delay)
            if schedule is not None:
                schedule[station.id] = retry_state[station.id][1]
            logger.warning("Radio %s : %s, nouvel essai dans %ss", station.id, reason, delay)

    await asyncio.gather(*(consume() for _ in range(settings.collector_concurrency)))
    await asyncio.to_thread(
        report, SessionLocal, "collector", "completed", completed=completed, failed=failed
    )
    await asyncio.to_thread(diagnostics.safe_persist, diagnostics.prune, SessionLocal)
    return round_id


async def main():
    logger.info(
        "Collecteur démarré, intervalle=%ss, concurrence=%s",
        settings.poll_interval_seconds,
        settings.collector_concurrency,
    )
    next_maintenance = 0
    heartbeat = Path("data/collector.heartbeat")
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    heartbeat.touch()
    async with metadata_client() as client:
        retry_state, schedule = await asyncio.to_thread(restore_schedule)
        while True:
            try:
                round_id = await collect_round(client, retry_state, schedule)
                await diagnostics.process_probe(SessionLocal, client, round_id)
                if time.monotonic() >= next_maintenance:
                    await asyncio.to_thread(archive_old_plays)
                    next_maintenance = time.monotonic() + 86400
                heartbeat.touch()
            except Exception:
                await asyncio.to_thread(report, SessionLocal, "collector", "error")
                logger.exception("Cycle de collecte interrompu, nouvel essai au prochain cycle")
            await asyncio.sleep(next_delay(schedule, time.monotonic()))


async def youtube_loop():
    from fastapi import HTTPException

    from app.services.youtube import worker_round

    while True:
        if settings.youtube_api_key.get_secret_value():
            await asyncio.to_thread(
                report, SessionLocal, "youtube", "started", total=1, completed=0, failed=0
            )
            try:
                done = await worker_round(SessionLocal)
                await asyncio.to_thread(
                    report,
                    SessionLocal,
                    "youtube",
                    "completed",
                    total=int(done),
                    completed=int(done),
                )
            except HTTPException as error:
                await asyncio.to_thread(
                    report,
                    SessionLocal,
                    "youtube",
                    "waiting" if error.status_code == 429 else "error",
                    failed=1,
                )
            except Exception:
                await asyncio.to_thread(report, SessionLocal, "youtube", "error", failed=1)
        else:
            await asyncio.to_thread(
                report, SessionLocal, "youtube", "waiting", total=0, completed=0
            )
        await asyncio.sleep(30)


if __name__ == "__main__":

    async def run():
        await asyncio.gather(main(), enrichment_loop(SessionLocal), youtube_loop())

    asyncio.run(run())
