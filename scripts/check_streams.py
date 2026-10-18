"""Sonde chaque source une fois, sans écrire dans la base."""

import asyncio

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import Station
from app.services.metadata import fetch_metadata


async def main():
    with SessionLocal() as db:
        stations = list(db.scalars(select(Station).where(Station.enabled.is_(True))))
    for station in stations:
        try:
            metadata = await fetch_metadata(station, settings.request_timeout_seconds)
            print(f"OK  {station.name}: {metadata.raw if metadata else 'pas de metadata'}")
        except Exception as exc:
            print(f"ERR {station.name}: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
