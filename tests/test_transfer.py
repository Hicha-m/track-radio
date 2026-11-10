import gzip
import json

import pytest
from sqlalchemy import func, select

from app.database import make_engine
from app.models import Base, Station
from scripts import transfer


def test_roundtrip_and_refuse_overwriting_destination(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path / 'transfer.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(transfer, "engine", engine)
    with engine.begin() as connection:
        connection.execute(
            Station.__table__.insert(),
            {"id": 7, "name": "Étoile", "slug": "etoile", "stream_url": "https://example.com"},
        )
    path = tmp_path / "backup.gz"
    transfer.export_data(path)
    with engine.begin() as connection:
        connection.execute(Station.__table__.delete())
    transfer.import_data(path)
    with engine.connect() as connection:
        assert connection.scalar(select(Station.name).where(Station.id == 7)) == "Étoile"
    with pytest.raises(ValueError, match="déjà"):
        transfer.import_data(path)
    engine.dispose()


def test_incomplete_import_rolls_back_all_rows(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path / 'transfer.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(transfer, "engine", engine)
    path = tmp_path / "incomplete.gz"
    with gzip.open(path, "wt") as output:
        output.write(
            json.dumps(
                {
                    "table": "stations",
                    "row": {
                        "id": 1,
                        "name": "Test",
                        "slug": "test",
                        "stream_url": "https://example.com",
                        "enabled": True,
                        "source_type": "icy",
                        "created_at": "2026-10-18T00:00:00",
                    },
                }
            )
            + "\n"
        )
    with pytest.raises(ValueError, match="incomplet"):
        transfer.import_data(path)
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Station)) == 0
    engine.dispose()


def test_metadata_and_manual_correction_survive_backup(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session

    from app.models import TrackMetadata
    from app.services.catalog import ensure_entry

    engine = make_engine(f"sqlite:///{tmp_path / 'metadata.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(transfer, "engine", engine)
    with Session(engine) as db:
        entry = ensure_entry(db, "Mosimann", "Soon")
        entry.manual = True
        entry.selected = {"provider": "deezer", "provider_id": "42"}
        entry.lyrics = {"plain": "Words", "provider": "lrclib"}
        key = entry.id
        db.commit()
    path = tmp_path / "metadata.gz"
    transfer.export_data(path)
    with engine.begin() as conn:
        conn.execute(TrackMetadata.__table__.delete())
        from app.models import MediaDiscovery

        conn.execute(MediaDiscovery.__table__.delete())
    transfer.import_data(path)
    with Session(engine) as db:
        restored = db.get(TrackMetadata, key)
        assert restored.manual
        assert restored.selected["provider_id"] == "42"
        assert restored.lyrics["plain"] == "Words"
        assert db.get(MediaDiscovery, key).status == "pending"
    engine.dispose()


def test_old_archive_without_metadata_imports(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(transfer, "engine", engine)
    path = tmp_path / "legacy.gz"
    with gzip.open(path, "wt") as output:
        output.write(json.dumps({"counts": {"stations": 0, "plays": 0}}) + "\n")
    transfer.import_data(path)
    engine.dispose()


def test_youtube_manual_choice_shared_quota_and_supervision_survive_backup(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from sqlalchemy.orm import Session

    from app.models import ServiceQuota, WorkerEvent, WorkerState, YouTubeMatch
    from app.services.youtube import queue

    engine = make_engine(f"sqlite:///{tmp_path / 'youtube.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(transfer, "engine", engine)
    now = datetime.now(UTC)
    with Session(engine) as db:
        queue(db, "a" * 64, "Artist", "Song")
        row = db.get(YouTubeMatch, "a" * 64)
        row.manual = True
        row.status = "matched"
        row.checked_at = now
        row.selected = {"id": "abcdefghijk", "title": "Fixture", "channel": "Fixture"}
        db.add(ServiceQuota(name="youtube", day="2026-11-06", used=23))
        db.add(
            WorkerState(
                name="youtube",
                state="idle",
                heartbeat_at=now,
                started_at=now,
                completed=1,
                failed=0,
                total=1,
                activity="completed",
            )
        )
        db.add(WorkerEvent(worker="youtube", at=now, code="completed"))
        db.commit()
    path = tmp_path / "youtube.gz"
    transfer.export_data(path)
    with engine.begin() as conn:
        for model in (WorkerEvent, WorkerState, ServiceQuota, YouTubeMatch):
            conn.execute(model.__table__.delete())
    transfer.import_data(path)
    with Session(engine) as db:
        restored = db.get(YouTubeMatch, "a" * 64)
        assert restored.manual and restored.selected["id"] == "abcdefghijk"
        assert restored.checked_at is not None
        assert db.get(ServiceQuota, "youtube").used == 23
        assert db.get(WorkerState, "youtube").completed == 1
        assert db.scalar(select(func.count()).select_from(WorkerEvent)) == 1
    engine.dispose()
