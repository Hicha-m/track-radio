import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.orm import Session

from app.models import Play, Station
from app.services.collector import record_metadata
from app.services.metadata import fetch_metadata


def fetch(station, payload):
    requests = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield json.dumps(payload).encode()

    async def run():
        def response(request):
            requests.append(request)
            return httpx.Response(200, stream=Body())

        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            return await fetch_metadata(station, 5, client)

    return asyncio.run(run()), requests


@pytest.mark.parametrize("source", ["rfm", "hit-west"])
def test_json_event_time_dynamic_url_restart_and_real_rebroadcast(db, source):
    is_rfm = source == "rfm"
    station = Station(
        name=source,
        slug=source,
        stream_url="https://example.test/live",
        source_type="json",
        metadata_url="https://example.test/history?date={now_ms}",
        json_artist_path="nowplaying.0.artist" if is_rfm else "0.title.artist",
        json_title_path="nowplaying.0.title" if is_rfm else "0.title.title",
        json_started_path="nowplaying.0.time" if is_rfm else "0.timestamp",
    )
    db.add(station)
    db.commit()
    at = datetime.now(UTC) - timedelta(minutes=10)

    def payload(date):
        song = {"artist": "Artist", "title": "Song"}
        return (
            {"nowplaying": [{**song, "time": date}]}
            if is_rfm
            else [{"title": song, "timestamp": date}]
        )

    first, requests = fetch(station, payload(at.isoformat()))
    assert requests[0].url.params["date"].isdigit()
    assert abs(int(requests[0].url.params["date"]) - datetime.now(UTC).timestamp() * 1000) < 5000
    assert first.source_started_ms == int(at.timestamp() * 1000)
    record_metadata(db, station, first)
    with Session(db.get_bind()) as restarted:
        current = restarted.get(Station, station.id)
        assert not record_metadata(restarted, current, first)[1]
        stale, _ = fetch(current, payload((at - timedelta(minutes=2)).isoformat()))
        assert not record_metadata(restarted, current, stale)[1]
        again, _ = fetch(current, payload((at + timedelta(minutes=4)).isoformat()))
        assert record_metadata(restarted, current, again)[1]
        assert (
            fetch(current, payload((datetime.now(UTC) + timedelta(hours=1)).isoformat()))[0] is None
        )
    assert db.query(Play).count() == 2
    missing, _ = fetch(station, payload(None))
    assert missing.source_started_ms is None


def test_enjoy_config_latest_event_restart_and_rebroadcast(db):
    from pathlib import Path

    from app.services.station_config import load_stations

    config = next(s for s in load_stations(Path("config/stations.json")) if s.slug == "enjoy-33")
    station = Station(**config.model_dump())
    db.add(station)
    db.commit()
    at = int((datetime.now(UTC) - timedelta(minutes=10)).timestamp())

    def payload(start):
        return {
            "success": True,
            "result": [
                {"track_artist": "Test Artist", "track_title": "Test Song", "track_played": start},
                {
                    "track_artist": "Older Artist",
                    "track_title": "Older Song",
                    "track_played": at - 300,
                },
            ],
        }

    first, _ = fetch(station, payload(at))
    assert first.artist == "Test Artist" and first.title == "Test Song"
    assert first.source_started_ms == at * 1000
    assert record_metadata(db, station, first)[1]
    with Session(db.get_bind()) as restarted:
        current = restarted.get(Station, station.id)
        assert not record_metadata(restarted, current, first)[1]
        again, _ = fetch(current, payload(at + 240))
        assert record_metadata(restarted, current, again)[1]
    assert db.query(Play).count() == 2
    assert all(p.title == "Test Song" for p in db.query(Play).all())
