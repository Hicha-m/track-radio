import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.models import GlobalExclusion, Play, Station
from app.services.collector import record_metadata
from app.services.exclusions import exclusion_id
from app.services.metadata import TrackMetadata
from app.services.skyrock import NATIONAL_STREAM, parse_schedule

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
START = int(NOW.timestamp())


def event(uid=1, offset=-20, duration=100):
    start = START + offset
    return {
        "type": "record",
        "info": {
            "uid": uid,
            "title": "Fixture",
            "start_ts": str(start),
            "end_ts": str(start + duration),
            "start": datetime.fromtimestamp(start, UTC).isoformat(),
            "end": datetime.fromtimestamp(start + duration, UTC).isoformat(),
        },
        "artists": [{"name": "One"}, {"name": "Two"}],
        "likes": [{"user_id": "PRIVATE"}],
    }


def payload(events):
    return {
        "ts": START,
        "radios": {"natio": {"schedule": events}, "other": {"schedule": [event(999)]}},
    }


def test_select_by_time_not_order_and_drop_private_payload():
    m = parse_schedule(payload([event(2, 200), event(), event(3, -300)]), now=NOW)
    assert m.artist == "One, Two" and m.source_event_id == "skyrock:1"
    assert json.loads(m.raw) == {"start": (START - 20) * 1000, "event_id": "skyrock:1"}
    assert "PRIVATE" not in m.raw
    assert parse_schedule({"ts": START, "schedule": [event()]}, super_endpoint=False, now=NOW) == m


@pytest.mark.parametrize("events", [[], [event(2, 200)], [event(3, -300)], [{"type": "program"}]])
def test_no_current_record(events):
    assert parse_schedule(payload(events), now=NOW) is None


def test_reject_stale_clock_wrong_region_ambiguous_and_inconsistent_dates():
    p = payload([event()])
    p["ts"] -= 120
    assert parse_schedule(p, now=NOW) is None
    with pytest.raises(ValueError):
        parse_schedule({"ts": START, "radios": {"other": {}}}, now=NOW)
    with pytest.raises(ValueError):
        parse_schedule(payload([event(), event(2)]), now=NOW)
    e = event()
    e["info"]["start"] = "2026-01-01T11:59:40"
    with pytest.raises(ValueError):
        parse_schedule(payload([e]), now=NOW)


def test_uid_repeats_rebroadcast_transition_and_ident_filter(db):
    s = Station(name="Skyrock", slug="skyrock", stream_url=NATIONAL_STREAM)
    db.add(s)
    db.commit()
    first = TrackMetadata("One, Two", "Fixture", "One, Two - Fixture §1", "icy", None, "skyrock:1")
    play, created = record_metadata(db, s, first)
    assert created
    assert not record_metadata(db, s, parse_schedule(payload([event()]), now=NOW))[1]
    ident = TrackMetadata("SKYROCK", "Hit Skyrock", "fixture ident", "icy", is_music=False)
    decision = {}
    assert record_metadata(db, s, ident, decision=decision) == (None, False)
    assert decision["code"] == "non_music" and s.collection_state["play_id"] is None
    assert not record_metadata(db, s, first)[1]
    again = TrackMetadata(first.artist, first.title, "fixture", "icy", None, "skyrock:2")
    assert record_metadata(db, s, again)[1]
    assert db.scalar(select(func.count()).select_from(Play)) == 2
    db.add(
        GlobalExclusion(
            id=exclusion_id(first.artist, first.title),
            artist=first.artist,
            title=first.title,
            artist_normalized="one, two",
            title_normalized="fixture",
        )
    )
    db.commit()
    blocked = TrackMetadata(first.artist, first.title, "fixture", "icy", None, "skyrock:3")
    assert record_metadata(db, s, blocked)[0] is None
    assert db.scalar(select(func.count()).select_from(Play)) == 2


@pytest.mark.parametrize("ident", [False, True])
def test_fetchers_share_uid_and_recognize_ident(ident):
    import asyncio

    import httpx

    from app.services.metadata import fetch_metadata

    class Body(httpx.AsyncByteStream):
        def __init__(self, data):
            self.data = data

        async def __aiter__(self):
            yield self.data

    def respond(request):
        label = (
            b"StreamTitle='SKYROCK - Hit Skyrock';"
            if ident
            else b"StreamTitle='One, Two - Fixture \xc2\xa71';"
        )
        block = label.ljust(((len(label) + 15) // 16) * 16, b"\x00")
        return httpx.Response(
            200,
            headers={"icy-metaint": "1024"},
            stream=Body(b"a" * 1024 + bytes([len(block) // 16]) + block),
        )

    async def run():
        station = Station(stream_url=NATIONAL_STREAM, source_type="icy")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await fetch_metadata(station, 1, client)
        assert result.source_event_id == (None if ident else "skyrock:1")
        assert result.title == ("Hit Skyrock" if ident else "Fixture")
        assert result.is_music is not ident

    asyncio.run(run())
