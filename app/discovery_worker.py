"""A separate process for optional artwork and video discovery."""

import asyncio
import logging
from pathlib import Path
from time import monotonic

from app.database import SessionLocal
from app.services.free_media import Lookup
from app.services.media_discovery import backfill, worker_round
from app.services.musicbrainz_media import AGENT
from app.services.musicbrainz_media import Lookup as MusicBrainzLookup
from app.services.network import metadata_client
from app.services.supervision import report


async def run():
    heartbeat = Path("data/discovery.heartbeat")
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    heartbeat.touch()
    cursor, next_scan = "", 0
    async with metadata_client() as client:
        client.headers["User-Agent"] = AGENT
        lookup = Lookup(client, MusicBrainzLookup(client))
        while True:
            try:
                if monotonic() >= next_scan:
                    cursor = await asyncio.to_thread(backfill, SessionLocal, cursor)
                    if cursor is None:
                        cursor, next_scan = "", monotonic() + 21600
                await asyncio.to_thread(
                    report, SessionLocal, "discovery", "started", total=1, completed=0, failed=0
                )
                done = await worker_round(SessionLocal, lookup)
                await asyncio.to_thread(
                    report,
                    SessionLocal,
                    "discovery",
                    "error" if done == "error" else "completed",
                    total=int(bool(done)),
                    completed=int(bool(done) and done != "error"),
                    failed=int(done == "error"),
                )
            except Exception:
                await asyncio.to_thread(report, SessionLocal, "discovery", "error", failed=1)
                logging.getLogger(__name__).warning("Découverte temporairement indisponible")
            heartbeat.touch()
            await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(run())
