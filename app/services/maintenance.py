"""Archive avant suppression ; chaque lot est durable avant son commit SQL."""

import gzip
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select

from app.config import settings
from app.database import SessionLocal
from app.models import Play


def archive_old_plays():
    if not settings.retention_days:
        return
    cutoff = datetime.now(UTC) - timedelta(days=settings.retention_days)
    directory = Path(settings.archive_directory)
    directory.mkdir(parents=True, exist_ok=True)
    # Bound the work per day as well as memory usage. Large histories drain gradually.
    for _ in range(40):
        with SessionLocal() as db:
            rows = list(
                db.scalars(
                    select(Play)
                    .where(Play.detected_at < cutoff, Play.last_seen_at < cutoff)
                    .order_by(Play.detected_at, Play.id)
                    .limit(250)
                )
            )
            if not rows:
                return
            target = directory / f"plays-{datetime.now(UTC):%Y%m%d}-{uuid4().hex}.jsonl.gz"
            temporary = target.with_suffix(".tmp")
            try:
                with temporary.open("wb") as raw:
                    with gzip.GzipFile(fileobj=raw, mode="wb") as compressed:
                        for row in rows:
                            data = {
                                column.name: getattr(row, column.name)
                                for column in Play.__table__.columns
                            }
                            compressed.write((json.dumps(data, default=str) + "\n").encode())
                    raw.flush()
                    os.fsync(raw.fileno())
                os.replace(temporary, target)
                if os.name == "posix":
                    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                db.execute(
                    delete(Play)
                    .where(Play.id.in_([row.id for row in rows]), Play.last_seen_at < cutoff)
                    .execution_options(synchronize_session=False)
                )
                db.commit()
            finally:
                temporary.unlink(missing_ok=True)
