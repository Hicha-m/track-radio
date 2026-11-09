"""Bounded Skyrock checks from the worker network; never write plays or print payloads."""

import asyncio
import json
import time
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import CollectionStatus, Station
from app.services.collection_diagnostics import utc
from app.services.metadata import fetch_icy, read_bounded
from app.services.network import metadata_client
from app.services.skyrock import parse_schedule, stream_key

ENDPOINT = "https://skyrock.fm/api/v3/player/onair/super"


def snapshot():
    with SessionLocal() as db:
        rows = db.execute(
            select(Station, CollectionStatus)
            .outerjoin(CollectionStatus, Station.id == CollectionStatus.station_id)
            .where(Station.enabled.is_(True))
            .order_by(Station.id)
            .limit(settings.max_stations)
        ).all()
        now = datetime.now(UTC)
        return [
            {
                "radio": stream_key(station.stream_url),
                "source": station.source_type,
                "outcome": status.outcome if status else None,
                "http": status.http_status if status else None,
                "consecutive_errors": status.consecutive_errors if status else None,
                "observation_recent": bool(
                    status
                    and status.last_observation_at
                    and (now - utc(status.last_observation_at)).total_seconds() < 180
                ),
            }
            for station, status in rows
            if stream_key(station.stream_url)
        ]


async def diagnose():
    print("Skyrock worker state: " + json.dumps(snapshot(), sort_keys=True), flush=True)
    async with metadata_client() as client:
        try:
            async with asyncio.timeout(12):
                content = await read_bounded(client, ENDPOINT)
            payload = json.loads(content)
            # Output only station keys and validity flags; discard user/profile data.
            radios = payload.get("radios", {})
            for key, radio in radios.items():
                if not isinstance(key, str) or not key.replace("-", "").isalnum():
                    continue
                try:
                    track = parse_schedule(
                        {"ts": payload.get("ts"), "schedule": radio.get("schedule")},
                        super_endpoint=False,
                    )
                    result = {"radio": key, "current": track is not None}
                except (ValueError, KeyError, TypeError, AttributeError):
                    result = {"radio": key, "invalid": True}
                print("Skyrock JSON: " + json.dumps(result, sort_keys=True), flush=True)
        except Exception as error:
            print("Skyrock JSON error: " + type(error).__name__, flush=True)

        # Fixed configured mounts only, sequential and with a short connection lifetime.
        for mount in (
            "natio_mp3_128k",
            "francais_aac_128k",
            "klassiks_aac_128k",
            "hit_us_aac_128k",
            "nocturne_aac_128k",
            "parisidf_aac_128k",
            "lille_aac_128k",
            "bethune_aac_128k",
            "marseille_aac_128k",
            "abidjan_aac_128k",
            "alger_aac_128k",
            "plm_aac_128k",
            "casa_aac_128k",
            "tunis_aac_128k",
            "urban_music_aac_128k",
            "hit_skyrock_aac_128k",
            "1er_rap_aac_128k",
            "rap_rnb_aac_128k",
        ):
            started = time.monotonic()
            result = {"mount": mount}
            try:
                async with asyncio.timeout(8):
                    station = Station(stream_url=f"https://icecast.skyrock.net/s/{mount}")
                    track = await fetch_icy(station, client)
                    result["metadata"] = track is not None
                # A separate audio request checks exactly the listener's headers.
                async with asyncio.timeout(8):
                    async with client.stream("GET", station.stream_url) as response:
                        result["http"] = response.status_code
                        result["audio_type"] = response.headers.get("content-type", "").startswith(
                            ("audio/", "application/ogg")
                        )
                        response.raise_for_status()
                        async for chunk in response.aiter_raw():
                            if chunk:
                                result["audio_received"] = True
                                break
            except httpx.HTTPStatusError as error:
                result["http"] = error.response.status_code
                result["error"] = type(error).__name__
            except Exception as error:
                result["error"] = type(error).__name__
            result["duration_ms"] = round((time.monotonic() - started) * 1000)
            print("Skyrock stream: " + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    asyncio.run(diagnose())
