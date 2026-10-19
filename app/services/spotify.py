"""Spotify server authentication. Credentials and access tokens never reach the browser."""

import asyncio
from time import monotonic

import httpx

from app.config import settings
from app.services.catalog import fetch_json, safe_url

_token = None
_token_until = 0
_token_identity = None
_token_lock = asyncio.Lock()


async def access_token(client, force=False):
    global _token, _token_until, _token_identity
    client_id = settings.spotify_client_id.get_secret_value()
    secret = settings.spotify_client_secret.get_secret_value()
    if not client_id or not secret:
        raise ValueError("Spotify non configuré")
    identity = (client_id, secret)
    async with _token_lock:
        if not force and _token and _token_identity == identity and _token_until > monotonic():
            return _token
        data = await fetch_json(
            client,
            "https://accounts.spotify.com/api/token",
            method="POST",
            data={"grant_type": "client_credentials"},
            auth=httpx.BasicAuth(client_id, secret),
        )
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError("Réponse d'authentification Spotify invalide")
        _token_until = monotonic() + max(0, min(3600, int(data.get("expires_in", 0))) - 60)
        _token, _token_identity = token, identity
        return token


async def search_spotify(client, artist, title):
    params = {"q": f"{artist or ''} {title}".strip(), "type": "track", "limit": 10, "market": "FR"}
    for attempt in range(2):
        token = await access_token(client, force=bool(attempt))
        try:
            data = await fetch_json(
                client,
                "https://api.spotify.com/v1/search",
                params,
                headers={"Authorization": f"Bearer {token}"},
            )
            break
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 401 or attempt:
                raise
    return [
        {
            "provider": "spotify",
            "provider_id": str(track["id"]),
            "artist": ", ".join(a["name"] for a in track.get("artists", []))[:300],
            "title": str(track.get("name", ""))[:500],
            "album": str(track.get("album", {}).get("name", ""))[:500],
            "duration": round(track["duration_ms"] / 1000) if track.get("duration_ms") else None,
            "cover_url": safe_url((track.get("album", {}).get("images") or [{}])[0].get("url")),
            "preview_url": safe_url(track.get("preview_url")),
            "url": safe_url(track.get("external_urls", {}).get("spotify")),
            "isrc": track.get("external_ids", {}).get("isrc"),
        }
        for track in data.get("tracks", {}).get("items", [])[:10]
        if track and track.get("id") and track.get("name")
    ]


# Public links reuse results; denied access must not trigger a request per dialog.
_first_cache = {}
_first_lock = asyncio.Lock()
_unavailable_until = 0


async def first_result(artist, title):
    global _unavailable_until
    key = (artist, title)
    async with _first_lock:
        cached = _first_cache.get(key)
        if cached and cached[0] > monotonic():
            return cached[1]
        if _unavailable_until > monotonic():
            return {"status": "unavailable", "track": None}
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                tracks = await search_spotify(client, artist, title)
            result = {
                "status": "found" if tracks else "not_found",
                "track": tracks[0] if tracks else None,
            }
        except (httpx.HTTPError, ValueError, KeyError, TypeError, TimeoutError):
            _unavailable_until = monotonic() + 60
            return {"status": "unavailable", "track": None}
        if len(_first_cache) >= 256:
            _first_cache.pop(next(iter(_first_cache)))
        _first_cache[key] = (monotonic() + 3600, result)
        return result
