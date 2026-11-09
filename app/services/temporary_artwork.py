"""Optional station artwork: never changes recording identity or provider selection."""

import httpx

from app.services.catalog import exact_match, safe_url
from app.services.collection_diagnostics import trace_context
from app.services.skyrock import schedule_snapshot, shared_track, stream_key

radio_key = stream_key


def artwork_snapshot(payload, now=None):
    """Retain only dated current musical labels and track artwork, never user data."""
    try:
        _, tracks, _ = schedule_snapshot(payload, now)
    except (ValueError, TypeError):
        return {}
    return {
        key: {"artist": track.artist, "title": track.title, "url": track.cover_url, "end": end}
        for key, (track, end) in tracks.items()
        if track.is_music and track.cover_url
    }


async def skyrock_cover_for(station, track, client):
    key = radio_key(station.stream_url)
    if not key or not track.is_music:
        return None
    try:
        token = trace_context.set(None)
        try:
            item = await shared_track(client, key)
        finally:
            trace_context.reset(token)
        if (
            item
            and item.is_music
            and exact_match(track.artist, track.title, {"artist": item.artist, "title": item.title})
        ):
            return safe_url(item.cover_url)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, TimeoutError):
        pass  # An optional cover must never stop the collector.
    return None


async def cover_for(station, track, client):
    from app.services.mytuner import cover_for as mytuner_cover

    cover = await skyrock_cover_for(station, track, client)
    return cover or await mytuner_cover(station, track, client)
