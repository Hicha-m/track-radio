import json
from datetime import UTC, datetime

import pytest

from app.models import Play, Station
from app.services.station_selection import station_ids


def test_station_set_validation():
    assert station_ids("3,1,3") == (1, 3)
    assert station_ids("") == ()
    for value in ("0", "-1", "1,,2", "x", "1.2", "９", "1," * 101):
        with pytest.raises(ValueError):
            station_ids(value)


def test_ranking_and_export_share_station_set_and_personal_exclusions(db, client):
    radios = [
        Station(name=f"Radio {i}", slug=f"radio-{i}", stream_url="https://example.test")
        for i in range(3)
    ]
    db.add_all(radios)
    db.flush()
    for i, radio in enumerate(radios):
        for _ in range(i + 1):
            db.add(
                Play(
                    station_id=radio.id,
                    artist="Artist",
                    title=f"Song {i}",
                    source="icy",
                    raw_metadata="source",
                    detected_at=datetime.now(UTC),
                    last_seen_at=datetime.now(UTC),
                )
            )
    db.commit()
    params = {"station_ids": f"{radios[0].id},{radios[2].id}"}
    ranked = client.get("/api/stats/top", params=params)
    assert ranked.status_code == 200, ranked.text
    assert [r["title"] for r in ranked.json()] == ["Song 2", "Song 0"]
    exported = client.get(
        "/api/export", params={**params, "view": "tracks", "format": "json", "sort": "frequency"}
    )
    assert exported.status_code == 200, exported.text
    assert [r["title"] for r in exported.json()["items"]] == ["Song 2", "Song 0"]
    hidden = json.dumps([{"artist": "Artist", "title": "Song 2"}])
    assert [
        r["title"]
        for r in client.get("/api/stats/top", params={**params, "exclusions": hidden}).json()
    ] == ["Song 0"]
    assert len(client.get("/api/stats/top").json()) == 3
