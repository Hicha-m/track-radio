import gzip
import json

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, insert, select, text

from app.config import settings
from app.models import Base, Play, Station, TrackMetadata
from app.services.track_identity import identity
from scripts import transfer


def test_existing_history_migrates_in_batches_and_restores_from_old_backup(tmp_path, monkeypatch):
    old_path = tmp_path / "before.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{old_path}")
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0005")
    old = create_engine(settings.database_url)
    metadata = MetaData()
    station = Table("stations", metadata, autoload_with=old)
    plays = Table("plays", metadata, autoload_with=old)
    catalogue = Table("track_metadata", metadata, autoload_with=old)
    from datetime import datetime

    with old.begin() as conn:
        conn.execute(
            insert(station),
            {
                "id": 1,
                "name": "Radio",
                "slug": "radio",
                "stream_url": "https://example.com",
                "enabled": False,
                "source_type": "icy",
                "created_at": datetime(2026, 1, 1),
            },
        )
        conn.execute(
            insert(plays),
            [
                dict(
                    id=i,
                    station_id=1,
                    artist="Ártist",
                    title="  Song  ",
                    raw_metadata="original",
                    detected_at=datetime(2026, 1, 1),
                    last_seen_at=datetime(2026, 1, 1),
                    source="icy",
                )
                for i in range(1, 602)
            ],
        )
        conn.execute(
            insert(catalogue),
            {
                "id": identity("Ártist", "  Song  "),
                "artist": "Ártist",
                "title": "  Song  ",
                "status": "matched",
                "provider": "deezer",
                "selected": {"provider": "deezer", "provider_id": "42"},
                "lyrics": {"plain": "Words"},
                "manual": True,
                "candidates": [],
                "lyrics_candidates": [],
            },
        )
    monkeypatch.setattr(transfer, "engine", old)
    backup = tmp_path / "old.gz"
    transfer.export_data(backup)
    command.upgrade(cfg, "head")
    with old.connect() as conn:
        rows = conn.execute(select(Play.track_id, Play.title, Play.raw_metadata)).all()
        assert len(rows) == 601
        assert all(r.track_id == identity("Artist", "Song") for r in rows)
        assert all(r.title == "  Song  " and r.raw_metadata == "original" for r in rows)
        assert conn.scalar(select(Station.config_enabled)) is False
        assert conn.scalar(select(TrackMetadata.lyrics_status)) == "matched"
        assert conn.scalar(select(TrackMetadata.manual))
    command.downgrade(cfg, "0001")
    command.upgrade(cfg, "head")
    restored = create_engine(f"sqlite:///{tmp_path / 'restored.db'}")
    Base.metadata.create_all(restored)
    monkeypatch.setattr(transfer, "engine", restored)
    transfer.import_data(backup)
    with restored.connect() as conn:
        assert conn.scalar(select(Play.track_id)) == identity("Artist", "Song")
        assert conn.scalar(select(TrackMetadata.lyrics_status)) == "matched"
        assert conn.scalar(select(Station.config_enabled)) is False
    old.dispose()
    restored.dispose()


def test_new_enrichment_progress_and_overrides_survive_backup(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'roundtrip.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(transfer, "engine", engine)
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        db.add(
            Station(
                name="Radio",
                slug="radio",
                stream_url="https://example.com",
                enabled=False,
                enabled_override=False,
                config_enabled=True,
            )
        )
        db.add(
            TrackMetadata(
                id=identity("Artist", "Song"),
                artist="Artist",
                title="Song",
                automatic_done=True,
                lyrics_status="not_found",
                attempts={"deezer": {"status": "not_found"}},
                revision=7,
            )
        )
        db.commit()
    backup = tmp_path / "roundtrip.gz"
    transfer.export_data(backup)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM stations"))
        conn.execute(text("DELETE FROM track_metadata"))
    transfer.import_data(backup)
    with engine.connect() as conn:
        assert conn.scalar(select(Station.enabled_override)) is False
        assert conn.scalar(select(TrackMetadata.automatic_done))
        assert conn.scalar(select(TrackMetadata.revision)) == 7
        assert conn.scalar(select(TrackMetadata.attempts)) == {"deezer": {"status": "not_found"}}
    with gzip.open(backup, "rt") as source:
        assert all(json.loads(line) for line in source)
    engine.dispose()


def test_secondary_enrichment_migration_queues_only_automatic_matches_without_genres(
    tmp_path, monkeypatch
):
    from sqlalchemy.orm import Session

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'secondary.db'}")
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0007")
    engine = create_engine(settings.database_url)
    with Session(engine) as db:
        for title, status, manual, genre in [
            ("Queue", "matched", False, None),
            ("Manual", "matched", True, None),
            ("Missing", "not_found", False, None),
            ("Known", "matched", False, "Rap"),
        ]:
            db.add(
                TrackMetadata(
                    id=identity("Artist", title),
                    artist="Artist",
                    title=title,
                    status=status,
                    manual=manual,
                    primary_genre=genre,
                    automatic_done=True,
                    revision=4,
                )
            )
        db.commit()
    command.upgrade(cfg, "head")
    with Session(engine) as db:
        entries = {entry.title: entry for entry in db.scalars(select(TrackMetadata))}
        assert not entries["Queue"].automatic_done and entries["Queue"].revision == 5
        for title in ["Manual", "Missing", "Known"]:
            assert entries[title].automatic_done and entries[title].revision == 4
    engine.dispose()
