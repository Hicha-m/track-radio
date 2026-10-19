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
    transfer.import_data(path)
    with Session(engine) as db:
        restored = db.get(TrackMetadata, key)
        assert restored.manual
        assert restored.selected["provider_id"] == "42"
        assert restored.lyrics["plain"] == "Words"
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
