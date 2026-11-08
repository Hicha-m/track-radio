"""Optional station artwork: never changes recording identity or provider selection."""

import asyncio
import json
from time import monotonic

import httpx

from app.services.catalog import exact_match, safe_url
from app.services.collection_diagnostics import trace_context
from app.services.metadata import read_bounded
from app.services.skyrock import parse_schedule, stream_key

radio_key = stream_key

ENDPOINT = "https://skyrock.fm/api/v3/player/onair/super"
_cache = {}
_expires = 0
_lock = asyncio.Lock()


def artwork_snapshot(payload, now=None):
    """Retain only dated current musical labels and track artwork, never user data."""
    result = {}
    if not isinstance(payload, dict) or not isinstance(payload.get("radios"), dict):
        return result
    for key, radio in payload["radios"].items():
        try:
            track = parse_schedule(
                {"ts": payload.get("ts"), "schedule": radio.get("schedule")},
                super_endpoint=False,
                now=now,
            )
            if track is None or not track.is_music:
                continue
            for item in radio["schedule"]:
                info = item.get("info", {})
                if f"skyrock:{info.get('uid')}" != track.source_event_id:
                    continue
                cover = safe_url(
                    info.get("cover_big_uri") or info.get("cover_uri") or info.get("cover_mid_uri")
                )
                if cover:
                    result[key] = {
                        "artist": track.artist,
                        "title": track.title,
                        "url": cover,
                        "end": int(info["end_ts"]),
                    }
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return result


async def cover_for(station, track, client):
    global _cache, _expires
    key = radio_key(station.stream_url)
    if not key or not track.is_music:
        return None
    try:
        async with _lock:
            if monotonic() >= _expires:
                # Clear stale results even when the next request fails.
                _cache = {}
                _expires = monotonic() + 15
                token = trace_context.set(None)
                try:
                    async with asyncio.timeout(5):
                        content = await read_bounded(client, ENDPOINT)
                finally:
                    trace_context.reset(token)
                _cache = artwork_snapshot(json.loads(content))
        from datetime import UTC, datetime

        item = _cache.get(key)
        if (
            item
            and datetime.now(UTC).timestamp() < item["end"]
            and exact_match(track.artist, track.title, item)
        ):
            return item["url"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError, TimeoutError):
        pass  # An optional cover must never stop the collector.
    return None
