import json
import re
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
    db: Session, station: Station, metadata: TrackMetadata, *, decision: dict | None = None
) -> tuple[Play | None, bool]:
    def observed(code, changed=False):
        if decision is not None:
            decision.update(code=code, changed=changed)

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
    previous_event = (state or {}).get("event_id")
    if previous_event is None and last is not None:
        try:
            previous_event = json.loads(last.raw_metadata).get("event_id")
        except (ValueError, TypeError, AttributeError):
            from app.services.skyrock import NATIONAL_STREAM

            match = re.search(r"§(\d+)\s*$", last.raw_metadata or "")
            if station.stream_url == NATIONAL_STREAM and match:
                previous_event = f"skyrock:{int(match.group(1))}"
    seen = list((state or {}).get("seen_event_ids", []))
    if previous_event and previous_event not in seen:
        seen.append(previous_event)
    event_id = metadata.source_event_id
    if event_id and event_id in seen and event_id != previous_event:
        observed("stale")
        return last, False
    identity_fields = {
        "event_id": event_id,
        "seen_event_ids": (seen + ([event_id] if event_id and event_id not in seen else []))[-32:],
    }
    if not event_id and not previous_event and not seen:
        identity_fields = {}
    if not metadata.is_music:
        station.collection_state = {
            "artist": None,
            "title": None,
            "play_id": None,
            "start": None,
            "latest_start": (state or {}).get("latest_start", latest_play_start),
            **identity_fields,
        }
        db.commit()
        observed("non_music")
        return None, False
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
        observed("stale")
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
        and (not event_id or not previous_event or event_id == previous_event)
    )
    if event_id and event_id == previous_event and not same:
        observed("stale")
        return last, False
    if previous_start == metadata.source_started_ms and previous_start is not None and not same:
        observed("stale")
        return last, False  # One source event cannot become two plays after a label change.
    blocked = db.get(GlobalExclusion, exclusion_id(metadata.artist, metadata.title)) is not None
    if blocked:
        observed("excluded", changed=not same)
        observation = {
            **identity_fields,
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
        observed("unchanged")
        previous = last.last_seen_at
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=UTC)
        remember_source = metadata.source_started_ms is not None and previous_start is None
        remember_identity = bool(event_id and (state or {}).get("event_id") != event_id)
        if remember_identity and state:
            station.collection_state = {**state, **identity_fields}
        if remember_source:
            last.raw_metadata = metadata.raw
            if state:
                station.collection_state = {
                    **state,
                    **identity_fields,
                    "start": metadata.source_started_ms,
                    "latest_start": latest_start,
                }
        if (
            remember_source
            or remember_identity
            or (now - previous).total_seconds() >= settings.last_seen_write_seconds
        ):
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
        **identity_fields,
        "artist": metadata.artist,
        "title": metadata.title,
        "start": metadata.source_started_ms,
        "play_id": play.id,
        "latest_start": latest_start,
    }
    db.commit()
    db.refresh(play)
    observed("new", changed=True)
    return play, True
