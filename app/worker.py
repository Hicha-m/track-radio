import asyncio
import logging

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import Station
from app.services.collector import record_metadata
from app.services.metadata import fetch_metadata

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("collector")


async def collect_station(station_id: int):
    with SessionLocal() as db:
        station = db.get(Station, station_id)
        if not station:
            return
        try:
            metadata = await fetch_metadata(station, settings.request_timeout_seconds)
            if metadata:
                _, created = record_metadata(db, station, metadata)
                logger.info("%s: %s%s", station.name, metadata.raw, " (nouveau)" if created else "")
            else:
                logger.warning("%s: aucune métadonnée", station.name)
        except Exception:
            logger.exception("Échec de collecte pour %s", station.name)


async def main():
    logger.info("Collecteur démarré, intervalle=%ss", settings.poll_interval_seconds)
    while True:
        with SessionLocal() as db:
            ids = list(db.scalars(select(Station.id).where(Station.enabled.is_(True))))
        await asyncio.gather(*(collect_station(station_id) for station_id in ids))
        await asyncio.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())

