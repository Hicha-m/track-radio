import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.orm import Session

from app.models import Play, Station
from app.services.collector import record_metadata
from app.services.metadata import fetch_json, radiofrance_start


def metadata(radio, label, start):
    payload = {
        "now": {"firstLine": "Mouv'", "secondLine": label, "startTime": start, "endTime": None}
    }

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield json.dumps(payload).encode()

    async def fetch():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Body()))
        ) as client:
            return await fetch_json(radio, client)

    return asyncio.run(fetch())


def radio(db):
    result = Station(
        name="Mouv'",
        slug="mouv",
        stream_url="https://example.test/live",
        metadata_url="https://api.radiofrance.fr/livemeta/live/6/transistor_mouv_player",
        json_title_path="now.secondLine",
        source_type="json",
    )
    db.add(result)
    db.commit()
    return result


def test_mouv_reordered_responses_restart_and_true_rebroadcast(db):
    station = radio(db)
    start = 1792480000
    sequence = [
        ("A2H • Ossau-Iraty", start),
        ("Other • Song", start + 150),
        ("A2H • Ossau-Iraty", start),
        ("Other • Song", start + 150),
    ]
    for label, at in sequence:
        record_metadata(db, station, metadata(station, label, at))
    assert db.query(Play).count() == 2
    # A new session has no in-memory collector state, like a redeployment.
    with Session(db.get_bind()) as restarted:
        assert not record_metadata(
            restarted, station, metadata(station, "Other • Song", start + 150)
        )[1]
        assert record_metadata(restarted, station, metadata(station, "Other • Song", start + 400))[
            1
        ]
    assert db.query(Play).count() == 3
    first = db.query(Play).order_by(Play.id).first()
    assert first.artist == "A2H" and first.title == "Ossau-Iraty"
    assert json.loads(first.raw_metadata)["start"] == start * 1000


def test_missing_timestamp_and_label_changes_do_not_duplicate_same_event(db):
    station = radio(db)
    a = metadata(station, "Artist • Song", 1792480000)
    first, _ = record_metadata(db, station, a)
    assert not record_metadata(db, station, metadata(station, "Corrected • Label", 1792480000))[1]
    assert not record_metadata(db, station, metadata(station, "Artist • Song", None))[1]
    assert not record_metadata(db, station, a)[1]
    assert json.loads(first.raw_metadata)["start"] == 1792480000000
    assert db.query(Play).count() == 1


@pytest.mark.parametrize("value", [None, False, "", "bad", -1, float("inf"), "2026-01-01T00:00:00"])
def test_invalid_source_timestamps(value):
    assert radiofrance_start(value) is None


def test_epoch_and_iso_timestamp_units_and_future_response(db):
    for value in [
        1792480000,
        1792480000000,
        "1792480000",
        datetime.fromtimestamp(1792480000, UTC).isoformat(),
    ]:
        assert radiofrance_start(value) == 1792480000000
    assert metadata(radio(db), "Artist • Future", 9999999999) is None
