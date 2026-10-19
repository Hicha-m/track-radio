import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Play, Station
from app.services.metadata import TrackMetadata


def normalize(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def record_metadata(db: Session, station: Station, metadata: TrackMetadata) -> tuple[Play, bool]:
    now = datetime.now(UTC)
    last = db.scalars(
        select(Play).where(Play.station_id == station.id).order_by(Play.detected_at.desc()).limit(1)
    ).first()
    previous_start = None
    if metadata.source_started_ms is not None and last:
        try:
            previous_start = json.loads(last.raw_metadata).get("start")
        except (ValueError, TypeError, AttributeError):
            pass  # Older raw metadata contains plain artist/title text.
        if type(previous_start) is not int:
            previous_start = None
        if previous_start is not None and metadata.source_started_ms < previous_start:
            return last, False  # A stale source response is not another broadcast.
    same = (
        last
        and normalize(last.artist) == normalize(metadata.artist)
        and normalize(last.title) == normalize(metadata.title)
        and (previous_start is None or metadata.source_started_ms == previous_start)
    )
    if previous_start == metadata.source_started_ms and previous_start is not None and not same:
        return last, False  # One source event cannot become two plays after a label change.
    if same:
        previous = last.last_seen_at
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=UTC)
        remember_source = metadata.source_started_ms is not None and previous_start is None
        if remember_source:
            last.raw_metadata = metadata.raw
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
    db.commit()
    db.refresh(play)
    return play, True
