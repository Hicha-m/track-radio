import asyncio

from app.config import settings
from app.models import Station
from app.worker import collect_round


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
