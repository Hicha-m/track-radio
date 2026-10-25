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

    async def consume():
        while not queue.empty():
            station = queue.get_nowait()
            try:
                ok = await collect_station(station, client)
                if ok:
                    retry_state.pop(station.id, None)
                    continue
                reason = "aucune métadonnée"
            except Exception as error:
                reason = type(error).__name__
            failures = retry_state.get(station.id, (0, 0))[0] + 1
            delay = min(300, settings.poll_interval_seconds * 2 ** min(failures, 4))
            retry_state[station.id] = (failures, time.monotonic() + delay)
            logger.warning("Radio %s : %s, nouvel essai dans %ss", station.id, reason, delay)

    await asyncio.gather(*(consume() for _ in range(settings.collector_concurrency)))


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
                logger.exception("Cycle de collecte interrompu, nouvel essai au prochain cycle")
            await asyncio.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":

    async def run():
        await asyncio.gather(main(), enrichment_loop(SessionLocal))

    asyncio.run(run())
