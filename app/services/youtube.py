"""On-demand video discovery: return only embeddable, plausible song matches."""

import html
import re

import httpx
from fastapi import HTTPException

from app.config import settings
from app.services.search_text import normalize_search


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


async def search_remote(artist, title):
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
        raise HTTPException(503, "Recherche YouTube indisponible. Réessayez plus tard.") from None
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
    return result


def automatic_choice(artist, title, candidates):
    ranked = [
        (candidate_score(artist, title, {"title": c["title"], "channelTitle": c["channel"]}), c)
        for c in candidates
    ]
    ranked = sorted(
        ((score, c) for score, c in ranked if score is not None),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if (
        artist
        and ranked
        and ranked[0][0] >= 4
        and (len(ranked) == 1 or ranked[0][0] > ranked[1][0])
    ):
        return ranked[0][1]
    return None


def quota_day(now):
    from zoneinfo import ZoneInfo

    return now.astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()


def insert_once(db, model, values, keys):
    # SQLite and PostgreSQL both support conflict-safe creation across processes.
    if db.get_bind().dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    db.execute(insert(model).values(**values).on_conflict_do_nothing(index_elements=keys))


def queue(db, key, artist, title):
    from datetime import UTC, datetime

    from app.models import YouTubeMatch

    insert_once(
        db,
        YouTubeMatch,
        dict(
            track_id=key,
            artist=artist,
            title=title,
            status="pending",
            candidates=[],
            selected=None,
            manual=False,
            revision=0,
            requested_at=datetime.now(UTC),
        ),
        ["track_id"],
    )


def public(match):
    return {
        "status": match.status,
        "candidates": match.candidates,
        "selected": match.selected,
        "manual": match.manual,
        "checked_at": match.checked_at,
        "revision": match.revision,
        "retry_at": match.retry_at,
    }


async def discover(artist, title, db, key=None, refresh=False):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import or_, update

    from app.models import ServiceQuota, YouTubeMatch
    from app.services.track_identity import identity

    key = key or identity(artist, title)
    now = datetime.now(UTC)
    queue(db, key, artist, title)
    db.commit()
    db.expire_all()
    match = db.get(YouTubeMatch, key)
    if not refresh and match.status not in {"pending", "error"}:
        return public(match)
    if not settings.youtube_api_key.get_secret_value():
        raise HTTPException(503, "La recherche YouTube n’est pas encore configurée.")
    revision = match.revision
    manual, previous_selected = match.manual, match.selected
    claimed = db.execute(
        update(YouTubeMatch)
        .where(
            YouTubeMatch.track_id == key,
            YouTubeMatch.revision == revision,
            or_(YouTubeMatch.lease_until.is_(None), YouTubeMatch.lease_until <= now),
            or_(YouTubeMatch.retry_at.is_(None), YouTubeMatch.retry_at <= now),
        )
        .values(lease_until=now + timedelta(minutes=2))
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(429, "Recherche déjà en cours ou temporairement suspendue.")
    insert_once(db, ServiceQuota, dict(name="youtube", day=quota_day(now), used=0), ["name"])
    db.execute(
        update(ServiceQuota)
        .where(ServiceQuota.name == "youtube", ServiceQuota.day != quota_day(now))
        .values(day=quota_day(now), used=0)
    )
    reserved = db.execute(
        update(ServiceQuota)
        .where(
            ServiceQuota.name == "youtube",
            ServiceQuota.used < settings.youtube_searches_per_day,
            or_(ServiceQuota.retry_at.is_(None), ServiceQuota.retry_at <= now),
        )
        .values(used=ServiceQuota.used + 1)
    )
    if reserved.rowcount != 1:
        db.rollback()
        raise HTTPException(
            429, "Limite de recherches YouTube atteinte ou service en temporisation."
        )
    db.commit()  # No transaction/connection is held across network I/O.
    try:
        result = await search_remote(artist, title)
    except HTTPException:
        db.execute(
            update(YouTubeMatch)
            .where(YouTubeMatch.track_id == key, YouTubeMatch.revision == revision)
            .values(
                status=("matched" if previous_selected else "rejected") if manual else "error",
                lease_until=None,
                retry_at=now + timedelta(minutes=15),
            )
        )
        db.execute(
            update(ServiceQuota)
            .where(ServiceQuota.name == "youtube")
            .values(retry_at=now + timedelta(minutes=15))
        )
        db.commit()
        raise
    # A manual choice made during the request always wins.
    candidates = result["candidates"]
    db.execute(
        update(YouTubeMatch)
        .where(YouTubeMatch.track_id == key, YouTubeMatch.revision == revision)
        .values(
            status=("matched" if previous_selected else "rejected") if manual else result["status"],
            candidates=candidates,
            checked_at=now,
            selected=previous_selected
            if manual
            else candidates[0]
            if result["status"] == "matched"
            else None,
            lease_until=None,
            retry_at=None,
            revision=revision + 1,
        )
    )
    db.commit()
    db.expire_all()
    return public(db.get(YouTubeMatch, key))


async def worker_round(session_factory):
    from datetime import UTC, datetime

    from sqlalchemy import or_, select

    from app.models import YouTubeMatch
    from app.services.exclusions import exclusion_conditions

    with session_factory() as db:
        match = db.scalar(
            select(YouTubeMatch)
            .where(
                YouTubeMatch.status.in_(["pending", "error"]),
                or_(YouTubeMatch.retry_at.is_(None), YouTubeMatch.retry_at <= datetime.now(UTC)),
                *exclusion_conditions(
                    "",
                    db.get_bind().dialect.name == "sqlite",
                    artist=YouTubeMatch.artist,
                    title=YouTubeMatch.title,
                ),
            )
            .order_by(YouTubeMatch.requested_at.desc())
            .limit(1)
        )
        if not match:
            return False
        await discover(match.artist, match.title, db, match.track_id)
        return True
