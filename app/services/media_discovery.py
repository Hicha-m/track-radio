"""Durable, throttled fallback discovery with no external work in page requests."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update

from app.models import MediaDiscovery, TrackMetadata, YouTubeMatch
from app.services.catalog import safe_url
from app.services.exclusions import exclusion_conditions
from app.services.youtube import insert_once


def queue(db, key):
    insert_once(
        db,
        MediaDiscovery,
        {"track_id": key, "status": "pending", "requested_at": datetime.now(UTC), "evidence": {}},
        ["track_id"],
    )


def visible(db):
    return exclusion_conditions(
        "",
        db.get_bind().dialect.name == "sqlite",
        artist=TrackMetadata.artist,
        title=TrackMetadata.title,
    )


def backfill(factory, cursor):
    # A small keyset scan of catalogue IDs, never the broadcast history or metadata blobs.
    with factory() as db:
        keys = list(
            db.scalars(
                select(TrackMetadata.id)
                .where(TrackMetadata.id > cursor, *visible(db))
                .order_by(TrackMetadata.id)
                .limit(100)
            )
        )
        for key in keys:
            queue(db, key)
        db.commit()
    return keys[-1] if keys else None


def claim(factory, strategy=None):
    now = datetime.now(UTC)
    with factory() as db:
        due = or_(MediaDiscovery.retry_at.is_(None), MediaDiscovery.retry_at <= now)
        if strategy:
            due = or_(
                due,
                MediaDiscovery.evidence["strategy"].as_string().is_(None),
                MediaDiscovery.evidence["strategy"].as_string() != strategy,
            )
        task = db.scalar(
            select(MediaDiscovery)
            .join(TrackMetadata, TrackMetadata.id == MediaDiscovery.track_id)
            .where(
                due,
                or_(MediaDiscovery.lease_until.is_(None), MediaDiscovery.lease_until <= now),
                *visible(db),
            )
            .order_by(MediaDiscovery.requested_at.desc(), MediaDiscovery.track_id)
            .limit(1)
        )
        if not task:
            return None
        lease = now + timedelta(minutes=3)
        key = task.track_id
        result = db.execute(
            update(MediaDiscovery)
            .where(
                MediaDiscovery.track_id == key,
                or_(MediaDiscovery.lease_until.is_(None), MediaDiscovery.lease_until <= now),
                due,
            )
            .values(lease_until=lease)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            db.rollback()
            return None
        entry = db.get(TrackMetadata, key)
        video = db.get(YouTubeMatch, key)
        snapshot = {
            "key": key,
            "artist": entry.artist,
            "title": entry.title,
            "selected": dict(entry.selected or {}),
            "revision": entry.revision,
            "video_revision": video.revision if video else None,
            "want_cover": not entry.manual
            and not safe_url((entry.selected or {}).get("cover_url"))
            and not safe_url(entry.temporary_cover_url),
            "want_video": not (video and (video.manual or video.selected)),
            "lease": lease,
        }
        db.commit()
        return snapshot


def finish(factory, snapshot, result):
    now = datetime.now(UTC)
    with factory() as db:
        task = db.get(MediaDiscovery, snapshot["key"])
        # An expired claimant cannot overwrite a new claimant's progress.
        if (
            not task
            or task.lease_until != snapshot["lease"].replace(tzinfo=None)
            and task.lease_until != snapshot["lease"]
        ):
            return
        entry = db.scalar(
            select(TrackMetadata).where(TrackMetadata.id == snapshot["key"], *visible(db))
        )
        if entry and (cover := result.get("cover")) and snapshot["want_cover"]:
            if safe_url(cover.get("url")):
                db.execute(
                    update(TrackMetadata)
                    .where(
                        TrackMetadata.id == entry.id,
                        TrackMetadata.revision == snapshot["revision"],
                        TrackMetadata.manual.is_(False),
                        TrackMetadata.temporary_cover_url.is_(None),
                    )
                    .values(temporary_cover_url=cover["url"], revision=TrackMetadata.revision + 1)
                    .execution_options(synchronize_session=False)
                )
        if entry and (video := result.get("video")) and snapshot["want_video"]:
            from app.services.youtube import queue as queue_video

            if snapshot["video_revision"] is None:
                queue_video(db, entry.id, entry.artist, entry.title)
            db.execute(
                update(YouTubeMatch)
                .where(
                    YouTubeMatch.track_id == entry.id,
                    YouTubeMatch.revision == (snapshot["video_revision"] or 0),
                    YouTubeMatch.manual.is_(False),
                    or_(YouTubeMatch.lease_until.is_(None), YouTubeMatch.lease_until <= now),
                )
                .values(
                    selected=video,
                    candidates=[video],
                    status="matched",
                    checked_at=now,
                    retry_at=None,
                    revision=YouTubeMatch.revision + 1,
                )
                .execution_options(synchronize_session=False)
            )
        task.status = result["status"] if entry else "hidden"
        task.checked_at = now
        task.retry_at = now + (
            timedelta(minutes=15) if result["status"] == "error" else timedelta(days=1)
        )
        task.lease_until = None
        task.evidence = {
            "recordings": result.get("recordings", []),
            "cover": result.get("cover") or (task.evidence or {}).get("cover"),
            "video_source": (result.get("video") or {}).get("source")
            or (task.evidence or {}).get("video_source"),
            "strategy": result.get("strategy") or (task.evidence or {}).get("strategy"),
            "sources": result.get("sources", {}),
        }
        db.commit()


async def worker_round(factory, lookup):
    import asyncio

    import httpx
    from fastapi import HTTPException

    strategy = getattr(lookup, "strategy", None)
    snapshot = await asyncio.to_thread(claim, factory, strategy)
    if not snapshot:
        return False
    if not snapshot["want_cover"] and not snapshot["want_video"]:
        result = {"status": "ready"}
    else:
        try:
            async with asyncio.timeout(120):
                result = await lookup.find(
                    snapshot["artist"],
                    snapshot["title"],
                    snapshot["selected"],
                    want_cover=snapshot["want_cover"],
                    want_video=snapshot["want_video"],
                )
        except (
            httpx.HTTPError,
            HTTPException,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            TimeoutError,
        ):
            result = {"status": "error"}
    result["strategy"] = strategy
    await asyncio.to_thread(finish, factory, snapshot, result)
    return result["status"]
