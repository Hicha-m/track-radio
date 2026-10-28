"""On-demand video discovery: return only embeddable, plausible song matches."""

import asyncio
import html
import re
from collections import OrderedDict, deque
from time import monotonic

import httpx
from fastapi import HTTPException

from app.config import settings
from app.services.search_text import normalize_search

_cache = OrderedDict()
_lock = asyncio.Lock()
_requests = deque()


def words(value):
    return re.findall(r"[a-z0-9]+", normalize_search(value or ""))


def candidate_score(artist, title, snippet):
    caption = html.unescape(snippet.get("title", ""))
    tokens = words(caption)
    song = words(title)
    singer = words(artist)
    channel = "".join(words(snippet.get("channelTitle", "")))
    if not song or not set(song) <= set(tokens):
        return None
    if singer and not (set(singer) <= set(tokens) or "".join(singer) in channel):
        return None
    variants = {
        "live",
        "remix",
        "cover",
        "karaoke",
        "instrumental",
        "reaction",
        "rehearsal",
        "loop",
        "slowed",
        "sped",
        "concert",
    }
    if (variants & set(tokens)) - set(song):
        return None
    artist_channel = bool(singer) and channel in {
        "".join(singer),
        "".join(singer) + "vevo",
        "".join(singer) + "topic",
    }
    return 4 * artist_channel + 2 * ("official" in tokens) + bool(singer)


async def discover(artist, title):
    if not settings.youtube_api_key.get_secret_value():
        raise HTTPException(503, "La recherche YouTube n’est pas encore configurée.")
    key = (tuple(words(artist)), tuple(words(title)))
    async with _lock:
        cached = _cache.get(key)
        if cached and cached[0] > monotonic():
            _cache.move_to_end(key)
            return cached[1]
        while _requests and _requests[0] < monotonic() - 86400:
            _requests.popleft()
        if len(_requests) >= settings.youtube_searches_per_day:
            raise HTTPException(429, "Limite de recherches YouTube atteinte. Réessayez plus tard.")
        _requests.append(monotonic())
        try:
            async with httpx.AsyncClient(
                timeout=15,
                headers={
                    "X-Goog-Api-Key": settings.youtube_api_key.get_secret_value(),
                    "User-Agent": "RadioTracker/0.1",
                },
            ) as client:
                response = await client.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={
                        "part": "snippet",
                        "type": "video",
                        "maxResults": 5,
                        "q": f"{artist or ''} {title}",
                        "videoEmbeddable": "true",
                        "videoSyndicated": "true",
                        "regionCode": "FR",
                    },
                )
                response.raise_for_status()
                search = response.json()
                ids = [
                    row.get("id", {}).get("videoId", "")
                    for row in search.get("items", [])
                    if re.fullmatch(r"[A-Za-z0-9_-]{11}", row.get("id", {}).get("videoId", ""))
                ]
                rows = []
                if ids:
                    response = await client.get(
                        "https://www.googleapis.com/youtube/v3/videos",
                        params={"part": "snippet,status,contentDetails", "id": ",".join(ids)},
                    )
                    response.raise_for_status()
                    rows = response.json().get("items", [])
                    if not isinstance(rows, list):
                        raise ValueError("Invalid video response")
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            raise HTTPException(
                503, "Recherche YouTube indisponible. Réessayez plus tard."
            ) from None
        candidates = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            snippet = row.get("snippet", {})
            status = row.get("status", {})
            if not isinstance(snippet, dict) or not isinstance(status, dict):
                continue
            if not isinstance(snippet.get("title"), str) or not isinstance(
                snippet.get("channelTitle"), str
            ):
                continue
            details = row.get("contentDetails") or {}
            if not isinstance(details, dict):
                continue
            region = details.get("regionRestriction") or {}
            if not isinstance(region, dict):
                continue
            score = candidate_score(artist, title, snippet)
            if (
                score is None
                or row.get("id") not in ids
                or not status.get("embeddable")
                or status.get("privacyStatus") != "public"
                or snippet.get("liveBroadcastContent", "none") != "none"
                or "FR" in (region.get("blocked") or [])
                or ("allowed" in region and "FR" not in (region["allowed"] or []))
            ):
                continue
            candidates.append(
                {
                    "id": row["id"],
                    "title": html.unescape(snippet.get("title", "")),
                    "channel": html.unescape(snippet.get("channelTitle", "")),
                    "score": score,
                }
            )
        candidates.sort(key=lambda row: row["score"], reverse=True)
        unique = (
            bool(artist)
            and bool(candidates)
            and candidates[0]["score"] >= 4
            and (len(candidates) == 1 or candidates[0]["score"] > candidates[1]["score"])
        )
        result = {
            "status": "matched" if unique else "ambiguous" if candidates else "not_found",
            "candidates": [{k: v for k, v in row.items() if k != "score"} for row in candidates],
        }
        _cache[key] = (monotonic() + 21600, result)
        while len(_cache) > 128:
            _cache.popitem(last=False)
        return result
