import asyncio

import pytest
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import Station
from app.worker import collect_round


@pytest.fixture(autouse=True)
def worker_db(tmp_path, monkeypatch):
    from app.database import make_engine
    from app.models import Base

    engine = make_engine(f"sqlite:///{tmp_path / 'worker-round.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        "app.worker.SessionLocal", sessionmaker(bind=engine, expire_on_commit=False)
    )
    yield
    engine.dispose()


def test_round_bounds_concurrency_and_survives_bad_station(monkeypatch):
    stations = [Station(id=i, name=f"Radio {i}") for i in range(30)]
    active = peak = finished = 0

    async def collect(station, client):
        nonlocal active, peak, finished
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.001)
            if station.id == 1:
                raise ValueError("bad station")
            finished += 1
            return True
        finally:
            active -= 1

    monkeypatch.setattr("app.worker.load_stations", lambda: stations)
    monkeypatch.setattr("app.worker.collect_station", collect)
    retries = {}
    asyncio.run(collect_round(None, retries))
    assert peak <= settings.collector_concurrency
    assert finished == 29
    assert 1 in retries


def test_round_does_not_retry_failed_station_immediately(monkeypatch):
    station = Station(id=1, name="Failed")
    calls = 0

    async def collect(station, client):
        nonlocal calls
        calls += 1
        return False

    monkeypatch.setattr("app.worker.load_stations", lambda: [station])
    monkeypatch.setattr("app.worker.collect_station", collect)

    async def run():
        retries = {}
        await collect_round(None, retries)
        await collect_round(None, retries)

    asyncio.run(run())
    assert calls == 1


def test_network_call_does_not_hold_database_connection(tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from app.database import make_engine
    from app.models import Base
    from app.worker import collect_station, load_stations

    engine = make_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        db.add(Station(name="Test", slug="test", stream_url="https://example.com"))
        db.commit()
    monkeypatch.setattr("app.worker.SessionLocal", sessions)

    async def fetch(station, timeout, client):
        assert engine.pool.checkedout() == 0
        return None

    monkeypatch.setattr("app.worker.fetch_metadata", fetch)
    station = load_stations()[0]
    asyncio.run(collect_station(station, None))
    engine.dispose()


def test_schedule_removes_round_duration_without_reducing_interval(monkeypatch):
    station = Station(id=1, name="Fixture")
    clock = [100.0]
    starts = []
    monkeypatch.setattr("app.worker.load_stations", lambda: [station])
    monkeypatch.setattr("app.worker.time.monotonic", lambda: clock[0])

    async def collect(station, client):
        starts.append(clock[0])
        clock[0] += 2
        return True

    monkeypatch.setattr("app.worker.collect_station", collect)

    async def run():
        retries, schedule = {}, {}
        await collect_round(None, retries, schedule)
        assert schedule[1] == 120
        clock[0] = 119
        await collect_round(None, retries, schedule)
        assert len(starts) == 1
        clock[0] = 120
        await collect_round(None, retries, schedule)

    asyncio.run(run())
    assert starts == [100, 120]  # Old schedule: 100,122 for this two-second attempt.


def test_retry_after_isolates_station_and_survives_restart(worker_db, tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    import httpx

    from app.database import make_engine
    from app.models import Base, CollectionStatus
    from app.worker import restore_schedule

    engine = make_engine(f"sqlite:///{tmp_path / 'retry.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("app.worker.SessionLocal", factory)
    with factory() as db:
        db.add(Station(id=1, name="Bad", slug="bad", stream_url="https://example.test"))
        db.add(Station(id=2, name="Good", slug="good", stream_url="https://example.test"))
        db.commit()
    calls = []

    async def collect(station, client):
        calls.append(station.id)
        if station.id == 1:
            response = httpx.Response(
                429,
                headers={"Retry-After": "600"},
                request=httpx.Request("GET", "https://example.test"),
            )
            response.raise_for_status()
        return True

    monkeypatch.setattr("app.worker.collect_station", collect)

    async def run():
        retries, schedule = {}, {}
        await collect_round(None, retries, schedule)
        assert retries[1][1] - __import__("time").monotonic() > 590
        assert 2 not in retries
        await collect_round(None, retries, schedule)
        assert calls == [1, 2]

    asyncio.run(run())
    with factory() as db:
        db.add(
            CollectionStatus(
                station_id=1,
                outcome="http_error",
                consecutive_errors=1,
                next_attempt_at=datetime.now(UTC) + timedelta(seconds=600),
            )
        )
        db.commit()
    retries, schedule = restore_schedule()
    assert retries[1][0] == 1 and schedule[1] - __import__("time").monotonic() > 590
    engine.dispose()


def test_temporary_artwork_is_saved_only_for_accepted_observation():
    from app.models import GlobalExclusion
    from app.models import TrackMetadata as Catalogue
    from app.services.exclusions import exclusion_id, normalized_pair
    from app.services.metadata import TrackMetadata
    from app.services.track_identity import identity
    from app.worker import SessionLocal, save_metadata

    with SessionLocal() as db:
        station = Station(name="Artwork", slug="artwork", stream_url="https://example.test/live")
        db.add(station)
        db.commit()
        station_id = station.id
    cover = "https://media.skyrock.fm/covers/test.jpg"
    song = TrackMetadata("Artist", "Song", "fixture", "icy", cover_url=cover)
    assert save_metadata(station_id, song)
    with SessionLocal() as db:
        entry = db.get(Catalogue, identity("Artist", "Song"))
        assert entry.temporary_cover_url == cover and entry.selected is None
        artist, title = normalized_pair("Other", "Blocked")
        db.add(
            GlobalExclusion(
                id=exclusion_id("Other", "Blocked"),
                artist="Other",
                title="Blocked",
                artist_normalized=artist,
                title_normalized=title,
            )
        )
        db.commit()
    assert not save_metadata(
        station_id, TrackMetadata("Other", "Blocked", "fixture", "icy", cover_url=cover)
    )
    with SessionLocal() as db:
        assert db.get(Catalogue, identity("Other", "Blocked")) is None


def test_existing_artwork_and_global_blacklist_skip_optional_network():
    from app.models import GlobalExclusion
    from app.services.catalog import ensure_entry
    from app.services.exclusions import exclusion_id, normalized_pair
    from app.services.metadata import TrackMetadata
    from app.worker import SessionLocal, needs_artwork

    song = TrackMetadata("Artist", "Song", "fixture", "icy")
    assert needs_artwork(song)
    with SessionLocal() as db:
        entry = ensure_entry(db, song.artist, song.title)
        entry.temporary_cover_url = "https://example.test/temporary.jpg"
        db.commit()
    assert not needs_artwork(song)
    with SessionLocal() as db:
        entry = ensure_entry(db, song.artist, song.title)
        entry.temporary_cover_url = None
        entry.selected = {"cover_url": "https://example.test/official.jpg"}
        db.commit()
    assert not needs_artwork(song)
    with SessionLocal() as db:
        entry = ensure_entry(db, song.artist, song.title)
        entry.selected = None
        artist, title = normalized_pair(song.artist, song.title)
        db.add(
            GlobalExclusion(
                id=exclusion_id(song.artist, song.title),
                artist=song.artist,
                title=song.title,
                artist_normalized=artist,
                title_normalized=title,
            )
        )
        db.commit()
    assert not needs_artwork(song)
    assert not needs_artwork(TrackMetadata("Station", "Ident", "fixture", "icy", is_music=False))


def test_mytuner_cover_reaches_catalogue_then_yields_to_official_artwork(monkeypatch):
    import json
    from time import monotonic

    import httpx

    from app.models import Play
    from app.services import mytuner
    from app.services.catalog import ensure_entry, public_metadata
    from app.services.metadata import TrackMetadata
    from app.worker import SessionLocal, collect_station

    monkeypatch.setattr(
        mytuner,
        "_assets",
        (
            "",
            """
function DG() { this.execute=()=> 'HMAC radiofrance_web:'+'A'.repeat(43); }
""",
        ),
    )
    monkeypatch.setattr(mytuner, "_assets_until", monotonic() + 3600)
    monkeypatch.setattr(mytuner, "_observations", {})
    with SessionLocal() as db:
        radio = Station(name="Europe 2", slug="europe2", stream_url="https://example.test/live")
        db.add(radio)
        db.commit()
        db.expunge(radio)

    async def observation(*args):
        return TrackMetadata("Artist", "Song", "source observation", "icy")

    monkeypatch.setattr("app.worker.fetch_metadata", observation)

    def respond(request):
        assert str(request.url).startswith(mytuner.API)
        payload = {
            "error_code": 0,
            "no_metadata_available": False,
            "radio_metadata": {
                "metadata": "Artist - Song",
                "artwork_url_large": "https://example.test/temporary.jpg",
            },
        }
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(payload).encode()))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            assert await collect_station(radio, client)

    asyncio.run(run())
    with SessionLocal() as db:
        entry = ensure_entry(db, "Artist", "Song")
        assert public_metadata(entry)["cover_url"] == "https://example.test/temporary.jpg"
        assert db.query(Play).count() == 1
        assert not entry.selected  # A fallback never chooses the recording.
        entry.selected = {
            "provider": "itunes",
            "provider_id": "fixture",
            "cover_url": "https://example.test/official.jpg",
        }
        db.commit()
        assert public_metadata(entry)["cover_url"] == "https://example.test/official.jpg"
        assert db.query(Play).count() == 1
