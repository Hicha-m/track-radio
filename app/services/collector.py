from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Play, Station
from app.services.metadata import TrackMetadata


def normalize(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def record_metadata(db: Session, station: Station, metadata: TrackMetadata) -> tuple[Play, bool]:
    now = datetime.now(UTC)
    last = db.scalars(
        select(Play).where(Play.station_id == station.id).order_by(Play.detected_at.desc()).limit(1)
    ).first()
    same = (
        last
        and normalize(last.artist) == normalize(metadata.artist)
        and normalize(last.title) == normalize(metadata.title)
    )
    if same:
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

