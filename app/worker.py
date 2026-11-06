import asyncio
import logging
import time
from pathlib import Path

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import Station
from app.services.catalog import enrichment_loop, ensure_entry
from app.services.collector import record_metadata
from app.services.maintenance import archive_old_plays
from app.services.metadata import fetch_metadata
from app.services.network import metadata_client
from app.services.supervision import report

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


def save_metadata(station_id, metadata):
    with SessionLocal() as db:
        station = db.get(Station, station_id)
        if not station or not station.enabled:
            return False
        play, created = record_metadata(db, station, metadata)
        if play is not None:
            ensure_entry(db, play.artist, play.title)
        if created and play is not None and settings.youtube_api_key.get_secret_value():
            from app.services.youtube import queue

            queue(db, play.track_id, play.artist, play.title)
        db.commit()
        return created


async def collect_station(station: Station, client: httpx.AsyncClient):
    # Detached snapshot: no database connection is held during network I/O.
    metadata = await fetch_metadata(station, settings.request_timeout_seconds, client)
    if metadata is None:
        return False
    created = await asyncio.to_thread(save_metadata, station.id, metadata)
    if created:
        logger.info("Radio %s : nouveau passage", station.id)
    return True


async def collect_round(client, retry_state):
    stations = await asyncio.to_thread(load_stations)
    active = {station.id for station in stations}
    for station_id in set(retry_state) - active:
        retry_state.pop(station_id)
    queue = asyncio.Queue()
    for station in stations:
        if retry_state.get(station.id, (0, 0))[1] <= time.monotonic():
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
            try:
                ok = await collect_station(station, client)
                if ok:
                    retry_state.pop(station.id, None)
                    completed += 1
                    await asyncio.to_thread(
                        report, SessionLocal, "collector", "metadata", completed=completed
                    )
                    continue
                reason = "aucune métadonnée"
            except Exception as error:
                reason = type(error).__name__
            failed += 1
            await asyncio.to_thread(report, SessionLocal, "collector", "metadata", failed=failed)
            failures = retry_state.get(station.id, (0, 0))[0] + 1
            delay = min(300, settings.poll_interval_seconds * 2 ** min(failures, 4))
            retry_state[station.id] = (failures, time.monotonic() + delay)
            logger.warning("Radio %s : %s, nouvel essai dans %ss", station.id, reason, delay)

    await asyncio.gather(*(consume() for _ in range(settings.collector_concurrency)))
    await asyncio.to_thread(
        report, SessionLocal, "collector", "completed", completed=completed, failed=failed
    )


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
        retry_state = {}
        while True:
            try:
                await collect_round(client, retry_state)
                if time.monotonic() >= next_maintenance:
                    await asyncio.to_thread(archive_old_plays)
                    next_maintenance = time.monotonic() + 86400
                heartbeat.touch()
            except Exception:
                await asyncio.to_thread(report, SessionLocal, "collector", "error")
                logger.exception("Cycle de collecte interrompu, nouvel essai au prochain cycle")
            await asyncio.sleep(settings.poll_interval_seconds)


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
