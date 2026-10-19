"""Catalogue durable. Aucun appel fournisseur dans les lectures du site."""

import asyncio
import hashlib
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from time import monotonic
from urllib.parse import urlsplit

import httpx
from sqlalchemy import case, or_, select

from app.models import Play, TrackMetadata
from app.services.search_text import normalize_search
from app.services.stats import cached_top

logger = logging.getLogger(__name__)
_provider_pauses = {}


class ProviderCooldown(ValueError):
    pass


def retry_seconds(value):
    try:
        return max(60, min(86400, int(value)))
    except (TypeError, ValueError):
        try:
            return max(
                60,
                min(86400, int((parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())),
            )
        except (TypeError, ValueError, OverflowError):
            return 900


def identity(artist, title):
    # Preserve version and featuring information in our identity; never merge broadcasts.
    values = [" ".join(normalize_search(v).split()) for v in (artist, title)]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def folded(value):
    return re.sub(r"[^a-z0-9]+", " ", normalize_search(value)).strip()


def without_feature(value):
    value = re.sub(r"[([]\s*(?:feat\.?|ft\.?)\s+[^)\]]*[)\]]", "", value, flags=re.I)
    return re.sub(r"\s+(?:feat\.?|ft\.?)\s+.*$", "", value, flags=re.I).strip()


def exact_match(artist, title, candidate):
    # Ignore a featured guest, but retain remix/live/radio-edit qualifiers.
    guest_pattern = r"(?:feat\.?|ft\.?)\s+([^\])]+)"
    wanted_guest = re.search(guest_pattern, f"{artist} {title}", re.I)
    found_guest = re.search(guest_pattern, f"{candidate['artist']} {candidate['title']}", re.I)
    if wanted_guest and (not found_guest or folded(wanted_guest[1]) != folded(found_guest[1])):
        return False
    return bool(artist) and (
        folded(without_feature(artist)) == folded(without_feature(candidate["artist"]))
        and folded(without_feature(title)) == folded(without_feature(candidate["title"]))
    )


def safe_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        return None
    parsed = urlsplit(value)
    if parsed.scheme == "https" and parsed.hostname and not parsed.username:
        return value
    return None


async def fetch_json(client, url, params=None, **options):
    host = urlsplit(url).hostname
    if _provider_pauses.get(host, 0) > monotonic():
        raise ProviderCooldown("Fournisseur en attente après limitation")
    async with (
        asyncio.timeout(12),
        client.stream(options.pop("method", "GET"), url, params=params, **options) as response,
    ):
        if response.status_code in {429, 503}:
            _provider_pauses[host] = monotonic() + retry_seconds(
                response.headers.get("retry-after")
            )
        response.raise_for_status()
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > 512 * 1024:
                raise ValueError("Réponse fournisseur trop volumineuse")
    data = json.loads(content)
    if isinstance(data, dict) and data.get("error"):
        raise ValueError("Erreur fournisseur")
    return data


async def search_provider(client, provider, artist, title):
    if provider == "spotify":
        from app.services.spotify import search_spotify

        return await search_spotify(client, artist, title)
    query = f"{artist or ''} {title}".strip()
    if provider == "deezer":
        data = await fetch_json(client, "https://api.deezer.com/search", {"q": query, "limit": 15})
        return [
            {
                "provider": provider,
                "provider_id": str(t["id"]),
                "artist": str(t.get("artist", {}).get("name", ""))[:300],
                "title": str(t.get("title", ""))[:500],
                "album": str(t.get("album", {}).get("title", ""))[:500],
                "duration": t.get("duration"),
                "cover_url": safe_url(t.get("album", {}).get("cover_big")),
                "preview_url": safe_url(t.get("preview")),
                "url": safe_url(t.get("link")),
                "isrc": t.get("isrc"),
            }
            for t in data.get("data", [])[:15]
            if t.get("id") and t.get("title")
        ]
    if provider == "itunes":
        data = await fetch_json(
            client,
            "https://itunes.apple.com/search",
            {
                "term": query,
                "entity": "song",
                "limit": 15,
                "country": "FR",
            },
        )
        return [
            {
                "provider": provider,
                "provider_id": str(t["trackId"]),
                "artist": str(t.get("artistName", ""))[:300],
                "title": str(t.get("trackName", ""))[:500],
                "album": str(t.get("collectionName", ""))[:500],
                "duration": round(t["trackTimeMillis"] / 1000)
                if t.get("trackTimeMillis")
                else None,
                "cover_url": safe_url(t.get("artworkUrl100")),
                "preview_url": safe_url(t.get("previewUrl")),
                "url": safe_url(t.get("trackViewUrl")),
                "isrc": None,
            }
            for t in data.get("results", [])[:15]
            if t.get("trackId") and t.get("trackName")
        ]
    raise ValueError("Fournisseur inconnu")


async def search_lyrics(client, artist, title):
    data = await fetch_json(
        client,
        "https://lrclib.net/api/search",
        {
            "artist_name": artist or "",
            "track_name": without_feature(title),
        },
    )
    return [
        {
            "id": str(t["id"]),
            "artist": t.get("artistName", ""),
            "title": t.get("trackName", ""),
            "duration": t.get("duration"),
            "plain": (t.get("plainLyrics") or "")[:60000],
            "synced": (t.get("syncedLyrics") or "")[:60000],
            "instrumental": bool(t.get("instrumental")),
            "provider": "lrclib",
        }
        for t in data[:15]
        if t.get("id")
    ]


def ensure_entry(db, artist, title):
    key = identity(artist, title)
    entry = db.get(TrackMetadata, key)
    if entry is None:
        entry = TrackMetadata(id=key, artist=artist, title=title)
        db.add(entry)
        db.flush()
    return entry


def public_metadata(entry):
    selected = entry.selected or {}
    return {
        "track_id": entry.id,
        "status": entry.status,
        "manual": entry.manual,
        **selected,
        "recording_id": (
            f"isrc:{selected['isrc']}"
            if selected.get("isrc")
            else f"{selected['provider']}:{selected['provider_id']}"
            if selected
            else None
        ),
    }


def attach_metadata(db, items):
    keys = {identity(t.get("artist"), t["title"]) for t in items}
    entries = {e.id: e for e in db.scalars(select(TrackMetadata).where(TrackMetadata.id.in_(keys)))}
    return [
        {
            **t,
            "metadata": public_metadata(entries[key])
            if key in entries
            else {"track_id": key, "status": "pending", "manual": False},
        }
        for t in items
        for key in [identity(t.get("artist"), t["title"])]
    ]


async def enrich_entry(client, artist, title, provider="deezer"):
    candidates = await search_provider(client, provider, artist, title)
    matches = [c for c in candidates if exact_match(artist, title, c)]
    # Different catalogue IDs may represent different recordings: leave the choice to a human.
    selected = matches[0] if len(matches) == 1 else None
    if selected and provider == "deezer":
        detail = await fetch_json(client, f"https://api.deezer.com/track/{selected['provider_id']}")
        selected = {
            **selected,
            "isrc": detail.get("isrc"),
            "preview_url": safe_url(detail.get("preview")) or selected["preview_url"],
        }
    lyrics = None
    if selected:
        try:
            lyrics_matches = [
                c
                for c in await search_lyrics(client, selected["artist"], selected["title"])
                if exact_match(selected["artist"], selected["title"], c)
                and selected.get("duration")
                and c.get("duration")
                and abs(c["duration"] - selected["duration"]) <= 2
            ]
            if len(lyrics_matches) == 1:
                lyrics = lyrics_matches[0]
        except (httpx.HTTPError, ValueError, TimeoutError):
            logger.warning("Paroles temporairement indisponibles")
    return candidates, selected, lyrics


def backfill_batch(session_factory, cursor):
    with session_factory() as db:
        rows = list(db.scalars(select(Play).where(Play.id > cursor).order_by(Play.id).limit(200)))
        for row in rows:
            ensure_entry(db, row.artist, row.title)
        db.commit()
        return rows[-1].id if rows else cursor


def ranking_priorities(session_factory):
    with session_factory() as db:
        tracks = cached_top(db, None, datetime.now(UTC) - timedelta(days=7), None, 100)
        priorities = {}
        for artist, title, count in tracks:
            entry = ensure_entry(db, artist, title)
            priorities[entry.id] = priorities.get(entry.id, 0) + count
        db.commit()
        return priorities


async def enrichment_round(client, session_factory, priorities=None):
    now = datetime.now(UTC)
    with session_factory() as db:
        entries = list(
            db.scalars(
                select(TrackMetadata)
                .where(
                    TrackMetadata.manual.is_(False),
                    or_(TrackMetadata.retry_at.is_(None), TrackMetadata.retry_at <= now),
                )
                .order_by(
                    case(priorities, value=TrackMetadata.id, else_=0).desc()
                    if priorities
                    else TrackMetadata.retry_at,
                    TrackMetadata.retry_at,
                    TrackMetadata.id,
                )
                .limit(2)
            )
        )
        pending = [(e.id, e.artist, e.title, e.provider) for e in entries]
    for key, artist, title, provider in pending:
        host = {
            "deezer": "api.deezer.com",
            "itunes": "itunes.apple.com",
            "spotify": "api.spotify.com",
        }[provider]
        if _provider_pauses.get(host, 0) > monotonic():
            continue
        try:
            candidates, selected, lyrics = await enrich_entry(client, artist, title, provider)
            status = "matched" if selected else "ambiguous" if candidates else "not_found"
            delay = timedelta(hours=6) if selected else timedelta(days=7)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, TimeoutError):
            candidates, selected, lyrics, status = [], None, None, "error"
            delay = timedelta(minutes=15)
            logger.warning("Enrichissement indisponible pour %s", key)
        with session_factory() as db:
            entry = db.get(TrackMetadata, key)
            if entry is None or entry.manual:
                continue
            # Keep usable metadata through temporary provider failures.
            if status != "error":
                old = entry.selected or {}
                new = selected or {}
                same_recording = (old.get("provider"), old.get("provider_id")) == (
                    new.get("provider"),
                    new.get("provider_id"),
                )
                entry.candidates, entry.selected = candidates, selected
                if lyrics or not same_recording:
                    entry.lyrics = lyrics
                entry.status = status
            elif not entry.selected:
                entry.status = status
            entry.checked_at, entry.retry_at = now, now + delay
            db.commit()


async def enrichment_loop(session_factory):
    cursor = 0
    next_priorities = 0
    priorities = {}
    async with httpx.AsyncClient(timeout=12, headers={"User-Agent": "RadioTracker/0.1"}) as client:
        while True:
            try:
                if monotonic() >= next_priorities:
                    priorities = await asyncio.to_thread(ranking_priorities, session_factory)
                    next_priorities = monotonic() + 60
                cursor = await asyncio.to_thread(backfill_batch, session_factory, cursor)
                await enrichment_round(client, session_factory, priorities)
            except Exception:
                logger.exception("Cycle d'enrichissement interrompu")
            await asyncio.sleep(20)
