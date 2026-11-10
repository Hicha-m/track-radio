"""Optional station artwork: never changes recording identity or provider selection."""

from time import monotonic

import httpx

from app.services.catalog import exact_match, folded, safe_url
from app.services.collection_diagnostics import trace_context
from app.services.skyrock import schedule_snapshot, shared_track, stream_key

radio_key = stream_key
_spotify_retry_at = 0


def artwork_confidence(artist, title, candidate):
    """Evidence score, not a statistical probability; keep guests and versions."""
    artist_match = bool(folded(artist or "")) and folded(artist) == folded(
        candidate.get("artist") or ""
    )
    title_match = bool(folded(title or "")) and folded(title) == folded(
        candidate.get("title") or ""
    )
    metadata = bool(
        candidate.get("provider_id")
        and candidate.get("provider") in {"deezer", "itunes", "spotify"}
        and candidate.get("album")
        and safe_url(candidate.get("url"))
        and safe_url(candidate.get("cover_url"))
    )
    return 45 * artist_match + 45 * title_match + 10 * metadata


def choose_artwork(artist, title, candidates):
    scored = [(artwork_confidence(artist, title, c), c) for c in candidates]
    matches = [c for score, c in scored if score == 100]
    covers = {c["cover_url"] for c in matches}
    if len(covers) != 1:
        return {
            "status": "ambiguous" if matches else "not_found",
            "score": max((score for score, _ in scored), default=0),
            "threshold": 100,
            "cover_url": None,
        }
    candidate = matches[0]
    return {
        "status": "matched",
        "score": 100,
        "threshold": 100,
        **{k: candidate.get(k) for k in ("provider", "provider_id", "album", "url", "cover_url")},
    }


async def catalog_artwork(client, artist, title, candidates):
    """Reuse catalogue evidence; Spotify is an optional background fallback only."""
    from app.config import settings
    from app.services.catalog import PROVIDER_ERRORS, search_provider

    global _spotify_retry_at
    result = choose_artwork(artist, title, candidates)
    if result["status"] in {"matched", "ambiguous"}:
        return result
    if not (
        settings.spotify_client_id.get_secret_value()
        and settings.spotify_client_secret.get_secret_value()
    ):
        return {**result, "spotify": "not_configured"}
    if monotonic() < _spotify_retry_at:
        return {**result, "status": "error"}
    try:
        tracks = await search_provider(client, "spotify", artist, title)
    except PROVIDER_ERRORS as error:
        _spotify_retry_at = monotonic() + (
            900
            if isinstance(error, httpx.HTTPStatusError) and error.response.status_code in {401, 403}
            else 60
        )
        return {**result, "status": "error"}
    return choose_artwork(artist, title, tracks)


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
