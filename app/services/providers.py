"""Additional manual catalogues. No credentials or untrusted pagination URLs leave here."""

import asyncio
import hashlib
import re
from threading import Lock
from time import monotonic
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx

from app.config import settings
from app.services.catalog import fetch_json, safe_url, without_feature

USER_AGENT = "RadioTracker/0.1 (https://github.com/Hicha-m/track-radio)"
_mb_lock = Lock()
_mb_next = 0.0


class ProviderNotConfigured(ValueError):
    pass


async def musicbrainz_slot():
    global _mb_next
    # One API worker in the deployed runtime; also serializes concurrent users.
    while True:
        with _mb_lock:
            delay = _mb_next - monotonic()
            if delay <= 0:
                _mb_next = monotonic() + 1.05
                return
        await asyncio.sleep(delay)


def mbid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def positive(value):
    try:
        number = int(value)
        return number if number > 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def mapping(value):
    return value if isinstance(value, dict) else {}


def object_data(value):
    if not isinstance(value, dict):
        raise ValueError("Réponse fournisseur invalide.")
    return value


def rows(value):
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def text(value, limit=500):
    return value.strip()[:limit] if isinstance(value, str) else ""


def lucene_phrase(value):
    return '"' + re.sub(r'([+\-!(){}\[\]^"~*?:\\/|&])', r"\\\1", value) + '"'


async def search_musicbrainz(client, artist, title, page, limit):
    query = f"recording:{lucene_phrase(without_feature(title))}"
    if artist:
        query += f" AND artist:{lucene_phrase(without_feature(artist))}"
    await musicbrainz_slot()
    data = await fetch_json(
        client,
        "https://musicbrainz.org/ws/2/recording/",
        {
            "query": query,
            "fmt": "json",
            "limit": limit,
            "offset": (page - 1) * limit,
        },
        headers={"User-Agent": USER_AGENT},
    )
    data = object_data(data)
    candidates = []
    for recording in rows(data.get("recordings"))[:limit]:
        key = mbid(recording.get("id"))
        title = text(recording.get("title"))
        if not key or not title:
            continue
        credits = rows(recording.get("artist-credit"))
        # Joinphrases are significant (e.g. ' & '), retain their whitespace.
        artist = "".join(
            text(c.get("name") or mapping(c.get("artist")).get("name"), 300)
            + (c.get("joinphrase") if isinstance(c.get("joinphrase"), str) else "")
            for c in credits
        )[:300]
        releases = [
            {
                "id": mbid(r.get("id")),
                "title": text(r.get("title")) or None,
                "date": text(r.get("date"), 40) or None,
            }
            for r in rows(recording.get("releases"))
            if mbid(r.get("id"))
        ][:3]
        release = releases[0] if releases else {}
        length = positive(recording.get("length"))
        genres = [
            text(g.get("name"), 100) for g in rows(recording.get("genres")) if text(g.get("name"))
        ][:20]
        isrcs = recording.get("isrcs") if isinstance(recording.get("isrcs"), list) else []
        candidates.append(
            {
                "provider": "musicbrainz",
                "provider_id": key,
                "title": title,
                "artist": artist,
                "album": text(release.get("title")) or None,
                "duration": round(length / 1000) if length else None,
                "release_id": mbid(release.get("id")),
                "releases": releases,
                "release_date": text(release.get("date"), 40) or None,
                "genres": genres,
                "genre_source": "musicbrainz" if genres else None,
                "isrc": next(
                    (text(v, 30) for v in isrcs if isinstance(v, str) and v.strip()), None
                ),
                "cover_url": None,
                "preview_url": None,
                "url": f"https://musicbrainz.org/recording/{key}",
            }
        )
    total = positive(data.get("count")) or 0
    return {"candidates": candidates, "page": page, "has_more": page * limit < total}


def archive_url(value):
    if isinstance(value, str) and value.startswith("http://"):
        host = urlsplit(value).hostname or ""
        if host in {"coverartarchive.org", "archive.org"} or host.endswith(".archive.org"):
            value = "https://" + value[7:]
    return safe_url(value)


async def cover_art(client, release_id):
    key = mbid(release_id)
    if not key:
        return {"status": "not_found", "cover_url": None}
    try:
        await musicbrainz_slot()
        data = await fetch_json(
            client,
            f"https://coverartarchive.org/release/{key}",
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        data = object_data(data)
        for image in rows(data.get("images")):
            if image.get("front") is not True:
                continue
            thumbnails = mapping(image.get("thumbnails"))
            url = (
                archive_url(thumbnails.get("500"))
                or archive_url(thumbnails.get("large"))
                or archive_url(image.get("image"))
            )
            if url:
                return {"status": "matched", "cover_url": url}
        return {"status": "not_found", "cover_url": None}
    except httpx.HTTPStatusError as error:
        return {
            "status": "not_found" if error.response.status_code == 404 else "error",
            "cover_url": None,
        }
    except (httpx.HTTPError, ValueError, KeyError, TypeError, TimeoutError):
        return {"status": "error", "cover_url": None}


def lastfm_url(value):
    # The documented response can contain HTTP links for Last.fm itself.
    if isinstance(value, str) and urlsplit(value).hostname in {"www.last.fm", "last.fm"}:
        if value.startswith("http://"):
            value = "https://" + value[7:]
    return safe_url(value)


async def search_lastfm(client, artist, title, page, limit):
    key = settings.lastfm_api_key.get_secret_value()
    if not key:
        raise ProviderNotConfigured(
            "Last.fm non configuré : renseignez LASTFM_API_KEY côté serveur."
        )
    params = {
        "method": "track.search",
        "track": without_feature(title),
        "api_key": key,
        "format": "json",
        "limit": limit,
        "page": page,
    }
    if artist:
        params["artist"] = without_feature(artist)
    data = await fetch_json(client, "https://ws.audioscrobbler.com/2.0/", params)
    result = mapping(object_data(data).get("results"))
    matches = mapping(result.get("trackmatches")).get("track") or []
    if isinstance(matches, dict):
        matches = [matches]
    candidates = []
    for track in rows(matches)[:limit]:
        name, singer = text(track.get("name")), text(track.get("artist"), 300)
        if not name:
            continue
        url = lastfm_url(track.get("url"))
        identifier = (
            mbid(track.get("mbid"))
            or hashlib.sha256(f"{singer}\0{name}\0{url or ''}".encode()).hexdigest()
        )
        images = rows(track.get("image"))
        cover = next(
            (safe_url(i.get("#text")) for i in reversed(images) if safe_url(i.get("#text"))), None
        )
        candidates.append(
            {
                "provider": "lastfm",
                "provider_id": identifier,
                "artist": singer,
                "title": name,
                "url": url,
                "cover_url": cover,
                "preview_url": None,
                "genres": [],
            }
        )
    total = positive(result.get("opensearch:totalResults"))
    if result.get("opensearch:totalResults") in (0, "0"):
        total = 0
    return {
        "candidates": candidates,
        "page": page,
        "has_more": page * limit < total if total is not None else len(matches) == limit,
    }


async def search_recordings(client, provider, artist, title, page=1, limit=15):
    if provider == "musicbrainz":
        return await search_musicbrainz(client, artist, title, page, limit)
    if provider == "lastfm":
        return await search_lastfm(client, artist, title, page, limit)
    raise ValueError("Fournisseur inconnu")


async def suggest_lyrics(client, artist, title):
    query = f"{without_feature(artist or '')} {without_feature(title)}".strip()
    data = await fetch_json(client, f"https://api.lyrics.ovh/suggest/{quote(query, safe='')}")
    result = []
    for track in rows(object_data(data).get("data"))[:15]:
        key = str(track.get("id") or "")
        title = text(track.get("title"))
        artist = text(mapping(track.get("artist")).get("name"), 300)
        if not key.isdigit() or not title or not artist:
            continue
        result.append(
            {
                "provider": "lyricsovh",
                "id": f"lyricsovh:{key}",
                "artist": artist,
                "title": title,
                "duration": positive(track.get("duration")),
                "album": text(mapping(track.get("album")).get("title")) or None,
                "cover_url": safe_url(mapping(track.get("album")).get("cover_big")),
                "plain": "",
                "synced": "",
                "instrumental": False,
                "lyrics_status": "pending",
            }
        )
    return result


async def load_lyrics(client, candidate):
    artist, title = (quote(candidate[k], safe="") for k in ("artist", "title"))
    try:
        data = await fetch_json(client, f"https://api.lyrics.ovh/v1/{artist}/{title}")
        lyrics = object_data(data).get("lyrics")
        if not isinstance(lyrics, str) or not lyrics.strip():
            return {**candidate, "plain": "", "lyrics_status": "not_found"}
        return {**candidate, "plain": lyrics.strip()[:60000], "lyrics_status": "matched"}
    except httpx.HTTPStatusError as error:
        return {
            **candidate,
            "lyrics_status": "not_found" if error.response.status_code == 404 else "error",
        }
    except (httpx.HTTPError, ValueError, KeyError, TypeError, TimeoutError):
        return {**candidate, "lyrics_status": "error"}
