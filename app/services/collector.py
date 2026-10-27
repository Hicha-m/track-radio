import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import GlobalExclusion, Play, Station
from app.services.exclusions import exclusion_id
from app.services.metadata import TrackMetadata


def normalize(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def stored_start(play: Play | None) -> int | None:
    if play is not None:
        try:
            start = json.loads(play.raw_metadata).get("start")
            return start if type(start) is int else None
        except (ValueError, TypeError, AttributeError):
            pass  # Older raw metadata contains plain artist/title text.
    return None


def record_metadata(
    db: Session, station: Station, metadata: TrackMetadata
) -> tuple[Play | None, bool]:
    # Serialize collectors for this station on databases supporting row locks.
    station = db.scalars(
        select(Station)
        .where(Station.id == station.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    now = datetime.now(UTC)
    last = db.scalars(
        select(Play)
        .where(Play.station_id == station.id)
        .order_by(Play.detected_at.desc(), Play.id.desc())
        .limit(1)
    ).first()
    latest_play_start = stored_start(last)
    state = station.collection_state
    if state:
        last = db.get(Play, state["play_id"]) if state.get("play_id") else None
    previous_start = state.get("start") if state else latest_play_start
    # An undated observation is a transition, but must not erase the latest
    # dated event. This also survives blacklisted announcements and restarts.
    known_starts = [previous_start, latest_play_start]
    if state:
        known_starts.append(state.get("latest_start"))
    latest_start = max((s for s in known_starts if type(s) is int), default=None)
    if (
        latest_start is not None
        and metadata.source_started_ms is not None
        and (
            metadata.source_started_ms < latest_start
            or (metadata.source_started_ms == latest_start and previous_start != latest_start)
        )
    ):
        return last, False  # A stale source response is not another broadcast.
    latest_start = (
        metadata.source_started_ms if metadata.source_started_ms is not None else latest_start
    )
    previous_artist = state["artist"] if state else last.artist if last else None
    previous_title = state["title"] if state else last.title if last else None
    same = (
        (state is not None or last is not None)
        and normalize(previous_artist) == normalize(metadata.artist)
        and normalize(previous_title) == normalize(metadata.title)
        and (
            previous_start is None
            or metadata.source_started_ms is None
            or metadata.source_started_ms == previous_start
        )
    )
    if previous_start == metadata.source_started_ms and previous_start is not None and not same:
        return last, False  # One source event cannot become two plays after a label change.
    blocked = db.get(GlobalExclusion, exclusion_id(metadata.artist, metadata.title)) is not None
    if blocked:
        observation = {
            "artist": metadata.artist,
            "title": metadata.title,
            "start": metadata.source_started_ms
            if metadata.source_started_ms is not None
            else previous_start
            if same
            else None,
            "play_id": None,
            "latest_start": latest_start,
        }
        if station.collection_state != observation:
            station.collection_state = observation
            db.commit()
        return None, False
    if same and last:
        previous = last.last_seen_at
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=UTC)
        remember_source = metadata.source_started_ms is not None and previous_start is None
        if remember_source:
            last.raw_metadata = metadata.raw
            if state:
                station.collection_state = {
                    **state,
                    "start": metadata.source_started_ms,
                    "latest_start": latest_start,
                }
        if remember_source or (now - previous).total_seconds() >= settings.last_seen_write_seconds:
            last.last_seen_at = now
            db.commit()
        return last, False
    play = Play(
        station_id=station.id,
        artist=metadata.artist,
        title=metadata.title,
        raw_metadata=metadata.raw,
        detected_at=now,
        last_seen_at=now,
        source=metadata.source,
    )
    db.add(play)
    db.flush()
    station.collection_state = {
        "artist": metadata.artist,
        "title": metadata.title,
        "start": metadata.source_started_ms,
        "play_id": play.id,
        "latest_start": latest_start,
    }
    db.commit()
    db.refresh(play)
    return play, True
