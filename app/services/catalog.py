"""Catalogue durable. Aucun appel fournisseur dans les lectures du site."""

import asyncio
import json
import logging
import re
from collections import OrderedDict
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from time import monotonic
from urllib.parse import urlsplit

import httpx
from sqlalchemy import case, or_, select

from app.models import Play, TrackMetadata
from app.services.search_text import normalize_search
from app.services.stats import cached_top
from app.services.track_identity import identity

logger = logging.getLogger(__name__)
_provider_pauses = {}
_album_cache = OrderedDict()


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
    # Do not let a featured guest dominate the provider's free-text search.
    # Selection still validates the original artist, title, guest and version.
    query = f"{without_feature(artist or '')} {without_feature(title)}".strip()
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
                "genres": [str(t["primaryGenreName"])[:100]] if t.get("primaryGenreName") else [],
                "genre_source": "itunes",
                "release_date": str(t["releaseDate"])[:40] if t.get("releaseDate") else None,
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
            "artist_name": without_feature(artist or ""),
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
        if t.get("id") and (t.get("plainLyrics") or t.get("syncedLyrics") or t.get("instrumental"))
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
        "lyrics_status": entry.lyrics_status,
        "primary_genre": entry.primary_genre,
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


PROVIDER_ERRORS = (httpx.HTTPError, ValueError, KeyError, TypeError, TimeoutError)


async def recording_details(client, selected):
    provider = selected["provider"]
    if provider == "itunes":
        if "genres" not in selected:
            data = await fetch_json(
                client,
                "https://itunes.apple.com/lookup",
                {
                    "id": selected["provider_id"],
                    "entity": "song",
                    "country": "FR",
                },
            )
            rows = [
                t
                for t in data.get("results", [])
                if str(t.get("trackId")) == selected["provider_id"]
            ]
            genre = rows[0].get("primaryGenreName") if len(rows) == 1 else None
            selected = {
                **selected,
                "genres": [str(genre)[:100]] if genre else [],
                "genre_source": "itunes",
            }
        return selected
    if provider != "deezer":
        return selected
    if "album_id" not in selected:
        detail = await fetch_json(client, f"https://api.deezer.com/track/{selected['provider_id']}")
        selected = {
            **selected,
            "isrc": detail.get("isrc"),
            "preview_url": safe_url(detail.get("preview")) or selected.get("preview_url"),
            "album_id": detail.get("album", {}).get("id"),
            "release_date": str(detail["release_date"])[:40]
            if detail.get("release_date")
            else None,
            "featured_artists": [
                str(c["name"])[:300]
                for c in detail.get("contributors", [])
                if c.get("name")
                and c.get("role") in {None, "Main", "Featured"}
                and folded(c["name"]) != folded(selected["artist"])
            ][:20],
        }
    album_id = selected["album_id"]
    genres = []
    if album_id and str(album_id).isdigit():
        cached = _album_cache.get(str(album_id))
        if cached and cached[0] > monotonic():
            genres = cached[1]
        else:
            try:
                album = await fetch_json(client, f"https://api.deezer.com/album/{album_id}")
                genres = [
                    str(g["name"])[:100]
                    for g in album.get("genres", {}).get("data", [])
                    if g.get("name")
                ][:20]
                _album_cache[str(album_id)] = (monotonic() + 86400, genres)
                while len(_album_cache) > 128:
                    _album_cache.popitem(last=False)
            except PROVIDER_ERRORS:
                # Keep the recording and retry only the missing album enrichment.
                return selected
    return {**selected, "genres": genres, "genre_source": "deezer"}


def lyrics_identity_match(selected, candidate):
    # A validated recording supplies the guest identity. LRCLIB often omits a
    # featuring suffix; conflicting guests and version qualifiers still fail.
    wanted_guest = re.search(
        r"(?:feat\.?|ft\.?)\s+([^\])]+)", f"{selected['artist']} {selected['title']}", re.I
    )
    found_guest = re.search(
        r"(?:feat\.?|ft\.?)\s+([^\])]+)", f"{candidate['artist']} {candidate['title']}", re.I
    )
    if found_guest and (not wanted_guest or folded(wanted_guest[1]) != folded(found_guest[1])):
        return False
    return exact_match(
        without_feature(selected["artist"]),
        without_feature(selected["title"]),
        {
            **candidate,
            "artist": without_feature(candidate["artist"]),
            "title": without_feature(candidate["title"]),
        },
    )


async def lyrics_result(client, selected):
    candidates = await search_lyrics(client, selected["artist"], selected["title"])
    related = [c for c in candidates if lyrics_identity_match(selected, c)]
    matches = [
        c
        for c in related
        if selected.get("duration")
        and c.get("duration")
        and abs(c["duration"] - selected["duration"]) <= 2
        and (c.get("plain") or c.get("synced") or c.get("instrumental"))
    ]
    lyrics = matches[0] if len(matches) == 1 else None
    return candidates, lyrics, "matched" if lyrics else "ambiguous" if related else "not_found"


async def enrich_entry(client, artist, title, provider="deezer"):
    """Single-provider operation retained for explicit catalogue lookups."""
    candidates = await search_provider(client, provider, artist, title)
    matches = [c for c in candidates if exact_match(artist, title, c)]
    selected = await recording_details(client, matches[0]) if len(matches) == 1 else None
    lyrics = None
    if selected:
        try:
            _, lyrics, _ = await lyrics_result(client, selected)
        except PROVIDER_ERRORS:
            logger.warning("Paroles temporairement indisponibles")
    return candidates, selected, lyrics


async def enrich_progress(client, snapshot):
    """Only unresolved steps are retried; final negative results are durable."""
    result = deepcopy(snapshot)
    attempts = result["attempts"]
    selected = result["selected"]
    checked_details = False
    if not selected:
        for provider in ("deezer", "itunes"):
            previous = attempts.get(provider, {})
            if previous.get("status") in {"not_found", "ambiguous"}:
                continue
            try:
                candidates = await search_provider(
                    client, provider, result["artist"], result["title"]
                )
                matches = [
                    c for c in candidates if exact_match(result["artist"], result["title"], c)
                ]
                selected = (
                    await recording_details(client, matches[0]) if len(matches) == 1 else None
                )
                checked_details = bool(selected)
                attempts[provider] = {
                    "status": "matched" if selected else "ambiguous" if matches else "not_found",
                    "candidates": candidates,
                }
                if selected:
                    break
            except PROVIDER_ERRORS:
                attempts[provider] = {**previous, "status": "error"}
        result["candidates"] = [
            c
            for provider in ("deezer", "itunes")
            for c in attempts.get(provider, {}).get("candidates", [])
        ]
        states = [attempts.get(p, {}).get("status") for p in ("deezer", "itunes")]
        result["status"] = (
            "matched"
            if selected
            else "error"
            if "error" in states
            else "ambiguous"
            if "ambiguous" in states
            else "not_found"
        )
        result["selected"] = selected
    if selected and selected["provider"] in {"deezer", "itunes"}:
        if "genres" not in selected and not checked_details:
            try:
                selected = await recording_details(client, selected)
            except PROVIDER_ERRORS:
                pass  # A failed optional lookup must preserve the validated recording.
        attempts["genres"] = {
            "status": "matched"
            if selected.get("genres")
            else "not_found"
            if "genres" in selected
            else "error"
        }
        result["selected"] = selected
    complement_failed = False
    if (
        selected
        and not selected.get("genres")
        and attempts.get("genres", {}).get("status") == "not_found"
    ):
        from app.services.catalog_completion import complete_missing

        secondary = "deezer" if selected["provider"] == "itunes" else "itunes"
        previous_search = attempts.get(secondary, {})
        known = (
            previous_search.get("candidates")
            if previous_search.get("status") in {"not_found", "ambiguous"}
            else None
        )
        selected, completion = await complete_missing(
            client, selected, attempts.get("complement"), known_candidates=known
        )

        result["selected"] = selected
        attempts["complement"] = completion
        complement_failed = completion["status"] == "error"
    if selected and result["lyrics_status"] in {"pending", "error"}:
        try:
            candidates, lyrics, status = await lyrics_result(client, selected)
            result.update(lyrics_candidates=candidates, lyrics=lyrics, lyrics_status=status)
            attempts["lrclib"] = {"status": status}
        except PROVIDER_ERRORS:
            result["lyrics_status"] = "error"
            attempts["lrclib"] = {"status": "error"}
    result["automatic_done"] = (
        result["status"] != "error"
        and not complement_failed
        and attempts.get("genres", {}).get("status") != "error"
        and (not selected or result["lyrics_status"] not in {"pending", "error"})
    )
    return result


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
                    TrackMetadata.automatic_done.is_(False),
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
        fields = (
            "artist",
            "title",
            "status",
            "selected",
            "candidates",
            "lyrics",
            "lyrics_candidates",
            "lyrics_status",
            "attempts",
        )
        pending = [
            (e.id, e.revision, deepcopy({f: getattr(e, f) for f in fields})) for e in entries
        ]
    for key, revision, snapshot in pending:
        result = await enrich_progress(client, snapshot)
        with session_factory() as db:
            # Atomic compare-and-set also covers a correction committed during provider I/O.
            from sqlalchemy import update

            selected = result["selected"] or {}
            values = {f: result[f] for f in fields if f not in {"artist", "title"}}
            values.update(
                automatic_done=result["automatic_done"],
                checked_at=now,
                retry_at=None if result["automatic_done"] else now + timedelta(minutes=15),
                primary_genre=(selected.get("genres") or [None])[0],
                provider=selected.get("provider", "deezer"),
                revision=revision + 1,
            )
            db.execute(
                update(TrackMetadata)
                .where(
                    TrackMetadata.id == key,
                    TrackMetadata.revision == revision,
                    TrackMetadata.manual.is_(False),
                )
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            db.commit()
            db.expire_all()


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
