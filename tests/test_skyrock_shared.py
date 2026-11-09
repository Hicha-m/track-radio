"""Shared Skyrock collection: exact station, freshness, retries and recording identity."""

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.models import GlobalExclusion, Play, Station
from app.services import skyrock
from app.services.collection_diagnostics import Trace, trace_context
from app.services.collector import record_metadata
from app.services.exclusions import exclusion_id, normalized_pair
from app.services.metadata import TrackMetadata, fetch_metadata
from app.services.station_config import load_stations
from app.services.temporary_artwork import cover_for

NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)
COVER = "https://media.skyrock.fm/covers/synthetic.jpg"


@pytest.fixture
def clock(monkeypatch):
    value = [0]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW + timedelta(seconds=value[0])

    monkeypatch.setattr(skyrock, "datetime", Clock)
    monkeypatch.setattr(skyrock, "monotonic", lambda: value[0])
    return value


def payload(keys=("francais", "hit-us", "klassiks", "nocturne"), *, at=0, uid=1):
    start, end = NOW + timedelta(seconds=at - 10), NOW + timedelta(seconds=at + 100)
    return {
        "ts": int((NOW + timedelta(seconds=at)).timestamp()),
        "radios": {
            key: {
                "schedule": [
                    {
                        "type": "record",
                        "artists": [{"name": "Artist"}],
                        "likes": [{"user_id": "PRIVATE", "avatar": "SECRET"}],
                        "info": {
                            "uid": uid,
                            "title": key,
                            "start_ts": str(int(start.timestamp())),
                            "end_ts": str(int(end.timestamp())),
                            "start": start.isoformat(),
                            "end": end.isoformat(),
                            "cover_uri": COVER,
                        },
                    }
                ]
            }
            for key in keys
        },
    }


def response(data):
    return httpx.Response(200, stream=httpx.ByteStream(json.dumps(data).encode()))


def stations():
    return [s for s in load_stations("config/stations.json") if s.slug.startswith("skyrock")]


def test_all_configured_radios_and_artwork_share_one_request(clock):
    async def run():
        configured = stations()
        keys = [skyrock.stream_key(s.stream_url) for s in configured]
        calls = []

        async def respond(request):
            calls.append(str(request.url))
            await asyncio.sleep(0.001)  # All callers contend for the same in-flight request.
            return response(payload(keys))

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            results = await asyncio.gather(
                *(fetch_metadata(Station(**s.model_dump()), 1, client) for s in configured)
            )
            for config, key, track in zip(configured, keys, results, strict=True):
                assert track.title == key
                assert track.source_event_id == "skyrock:1" and track.source == "json"
                assert track.cover_url == COVER
                assert await cover_for(Station(**config.model_dump()), track, client) == COVER
                assert "PRIVATE" not in track.raw and "SECRET" not in track.raw
            assert len(configured) == 18
            assert calls == [skyrock.SUPER_ENDPOINT]  # No Icecast audio connection.
            assert "PRIVATE" not in repr(skyrock._schedules[client])

    asyncio.run(run())


def test_cache_rechecks_event_end_and_provider_clock(clock):
    async def run():
        data = payload()
        data["radios"]["francais"]["schedule"][0]["info"].update(
            end_ts=str(int(NOW.timestamp()) + 5), end=(NOW + timedelta(seconds=5)).isoformat()
        )
        calls = []

        def respond(request):
            calls.append(request)
            return response(data)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            assert await skyrock.shared_track(client, "francais")
            clock[0] = 5
            assert await skyrock.shared_track(client, "francais") is None
            assert len(calls) == 1  # Expired title is not replayed from an otherwise valid cache.
            clock[0] = 61
            assert await skyrock.shared_track(client, "hit-us") is None
            assert len(calls) == 2

    asyncio.run(run())


@pytest.mark.parametrize("status", [429, 503])
def test_rate_limit_shared_and_never_falls_back_to_audio(clock, status):
    async def run():
        calls = []

        def respond(request):
            calls.append(str(request.url))
            return httpx.Response(status, headers={"Retry-After": "600"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            for key in ["francais", "klassiks", "hit-us"]:
                trace = Trace()
                token = trace_context.set(trace)
                try:
                    with pytest.raises(httpx.HTTPStatusError) as error:
                        await skyrock.shared_track(client, key)
                    assert error.value.response.status_code == status
                    assert trace.responses[-1]["status"] == status
                    assert trace.summary["retry_after_seconds"] == 600
                finally:
                    trace_context.reset(token)
            clock[0] = 599
            with pytest.raises(httpx.HTTPStatusError) as error:
                await skyrock.shared_track(client, "nocturne")
            assert error.value.response.headers["Retry-After"] == "1"
            assert calls == [skyrock.SUPER_ENDPOINT]
            clock[0] = 600
            with pytest.raises(httpx.HTTPStatusError):
                await skyrock.shared_track(client, "nocturne")
            assert len(calls) == 2

    asyncio.run(run())


def test_failed_refresh_discards_previous_snapshot_and_shares_failure(clock):
    async def run():
        calls = []

        def respond(request):
            calls.append(request)
            if len(calls) > 1:
                raise httpx.ConnectError("synthetic")
            return response(payload())

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            assert await skyrock.shared_track(client, "francais")
            clock[0] = 15
            for key in ["francais", "klassiks"]:
                with pytest.raises(httpx.RequestError):
                    await skyrock.shared_track(client, key)
            assert len(calls) == 2
            assert not skyrock._schedules[client].tracks

    asyncio.run(run())


def test_invalid_or_missing_radio_never_uses_another_schedule(clock):
    async def run():
        data = payload()
        data["radios"]["klassiks"] = {"schedule": "invalid"}
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: response(data))
        ) as client:
            assert (await skyrock.shared_track(client, "francais")).title == "francais"
            with pytest.raises(ValueError):
                await skyrock.shared_track(client, "klassiks")
            assert await skyrock.shared_track(client, "missing") is None
            assert (await skyrock.shared_track(client, "nocturne")).title == "nocturne"

    asyncio.run(run())


def test_switch_from_icy_preserves_rebroadcasts_blacklist_and_history(db, clock):
    station = Station(
        name="Synthetic Skyrock", slug="synthetic", stream_url=stations()[5].stream_url
    )
    db.add(station)
    db.commit()
    icy = TrackMetadata("Artist", "francais", "Artist - francais §1", "icy", None, "skyrock:1")
    assert record_metadata(db, station, icy)[1]
    original = payload()
    _, items, _ = skyrock.schedule_snapshot(original, NOW)
    assert not record_metadata(db, station, items["francais"][0])[1]
    later = payload(at=120, uid=2)
    _, items, _ = skyrock.schedule_snapshot(later, NOW + timedelta(seconds=120))
    assert record_metadata(db, station, items["francais"][0])[1]
    assert not record_metadata(db, station, items["francais"][0])[1]
    # Stale snapshots cannot create an artificial repeat after a newer event.
    _, old, _ = skyrock.schedule_snapshot(original, NOW)
    assert not record_metadata(db, station, old["francais"][0])[1]
    artist, title = normalized_pair("Artist", "francais")
    db.add(
        GlobalExclusion(
            id=exclusion_id(artist, title),
            artist="Artist",
            title="francais",
            artist_normalized=artist,
            title_normalized=title,
        )
    )
    db.commit()
    later = payload(at=240, uid=3)
    _, items, _ = skyrock.schedule_snapshot(later, NOW + timedelta(seconds=240))
    assert record_metadata(db, station, items["francais"][0])[0] is None
    assert db.scalar(select(func.count()).select_from(Play)) == 2
    # Never copy private upstream fields into the database.
    assert all("PRIVATE" not in p.raw_metadata for p in db.scalars(select(Play)))


def test_snapshot_rejects_ambiguous_events_without_poisoning_other_stations(clock):
    data = payload()
    duplicate = copy.deepcopy(data["radios"]["francais"]["schedule"][0])
    duplicate["info"]["uid"] = 2
    data["radios"]["francais"]["schedule"].append(duplicate)
    _, tracks, invalid = skyrock.schedule_snapshot(data, NOW)
    assert invalid == {"francais"}
    assert "francais" not in tracks and "hit-us" in tracks
