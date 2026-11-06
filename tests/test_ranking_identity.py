"""Ranking, exports and detail must count the same preserved detections."""

import csv
import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.models import GlobalExclusion, Play, Station
from app.services.catalog import ensure_entry
from app.services.exclusions import exclusion_id, normalized_pair
from app.services.track_identity import identity


@pytest.fixture
def ranking_history(db):
    now = datetime(2026, 11, 6, 12, tzinfo=UTC)
    stations = [
        Station(name=f"Radio {i}", slug=f"radio-{i}", stream_url="https://example.test/live")
        for i in range(3)
    ]
    db.add_all(stations)
    db.flush()
    for index, (title, count, station, at) in enumerate(
        [
            ("Te rencontrer encore", 97, 0, now),
            ("TE RENCONTRER ENCORE", 67, 1, now),
            ("Te Rencontrer Encore", 58, 2, now),
            ("Te rencontrer encore", 201, 0, now - timedelta(days=30)),
            ("Te rencontrer encore (Live)", 4, 0, now),
            ("Other", 2, 0, now),
        ]
    ):
        entry = ensure_entry(db, "ESTL", title)
        entry.selected = {
            "provider": "deezer",
            "provider_id": str(index),
            "title": title,
            "artist": "ESTL",
            "isrc": "FRPZ92620190",  # A shared provider hint alone must not merge versions.
        }
        for i in range(count):
            db.add(
                Play(
                    station_id=stations[station].id,
                    artist="ESTL" if index % 2 else "estl",
                    title=title,
                    detected_at=at + timedelta(seconds=i),
                    last_seen_at=at + timedelta(seconds=i),
                    source="json",
                    raw_metadata=f"original {index}/{i}",
                )
            )
    db.commit()
    return now, stations, identity("ESTL", "Te rencontrer encore")


def test_ranking_groups_before_limit_and_matches_export_and_detail(client, db, ranking_history):
    now, stations, key = ranking_history
    scope = {
        "since": (now - timedelta(days=7)).isoformat(),
        "station_ids": f"{stations[0].id},{stations[1].id}",
    }
    top = client.get("/api/stats/top", params={**scope, "limit": 2}).json()
    assert [row["plays"] for row in top] == [164, 4]
    assert top[0]["metadata"]["track_id"] == key
    assert client.get(f"/api/catalog/{key}/radio", params=scope).json()["detections"] == 164
    assert client.get(f"/api/catalog/{key}/radio").json()["detections"] == 423
    assert client.get("/api/stats/top").json()[0]["plays"] == 423
    for page in (1, 2, 3):
        data = client.get(
            "/api/explore",
            params={**scope, "view": "tracks", "sort": "frequency", "page_size": 1, "page": page},
        ).json()
        assert [r["plays"] for r in data["items"]] == [[164], [4], [2]][page - 1]
        assert data["has_more"] is (page < 3)
    params = {**scope, "view": "tracks", "sort": "frequency", "limit": 2}
    exported = client.get("/api/export", params={**params, "format": "json"}).json()
    assert [r["plays"] for r in exported["items"]] == [164, 4]
    exported_csv = client.get("/api/export", params={**params, "format": "csv"})
    rows = list(
        csv.DictReader(io.StringIO(exported_csv.content.decode("utf-8-sig")), delimiter=";")
    )
    assert [int(row["plays"]) for row in rows] == [164, 4]
    drilldown = client.get("/api/explore", params={**scope, "track_id": key, "page_size": 100})
    assert len(drilldown.json()["items"]) == 100
    assert drilldown.json()["has_more"]
    selected_plays = client.get(
        "/api/export", params={**scope, "track_id": key, "view": "plays", "format": "json"}
    ).json()
    assert selected_plays["count"] == 164
    # No raw metadata or genuine rebroadcast was modified or deleted.
    assert db.scalar(select(func.count(Play.id))) == 429
    assert db.scalar(select(Play.raw_metadata).order_by(Play.id)) == "original 0/0"


@pytest.mark.parametrize("admin", [False, True])
def test_ranking_exclusions_combine_before_grouping_pagination_and_exports(
    client, db, ranking_history, admin
):
    _, _, key = ranking_history
    artist, title = normalized_pair("ESTL", "Te rencontrer encore")
    db.add(
        GlobalExclusion(
            id=exclusion_id(artist, title),
            artist=artist,
            title=title,
            artist_normalized=artist,
            title_normalized=title,
        )
    )
    db.commit()
    headers = {"Authorization": "Bearer test-admin-token"} if admin else {}
    scope = {"exclusions": json.dumps([{"artist": "ESTL", "title": "OTHER"}])}
    top = client.get("/api/stats/top", params={**scope, "limit": 1}, headers=headers).json()
    assert len(top) == 1 and top[0]["plays"] == 4
    detail = client.get(f"/api/catalog/{key}/radio", params=scope, headers=headers)
    assert detail.status_code == 404 or detail.json()["detections"] == 0
    result = client.get(
        "/api/explore", params={**scope, "view": "tracks", "page_size": 1}, headers=headers
    ).json()
    assert len(result["items"]) == 1 and not result["has_more"]
    exported = client.get(
        "/api/export", params={**scope, "view": "tracks", "format": "json"}, headers=headers
    ).json()
    assert exported["count"] == 1 and exported["items"][0]["plays"] == 4
    assert db.scalar(select(func.count(Play.id))) == 429


def test_detail_period_bounds_offsets_and_invalid_filters(client, ranking_history):
    now, stations, key = ranking_history
    params = {
        "station_id": stations[0].id,
        "since": "2026-11-06T13:00:00+01:00",
        "until": "2026-11-06T12:00:01Z",
    }
    assert client.get(f"/api/catalog/{key}/radio", params=params).json()["detections"] == 1
    top = client.get("/api/stats/top", params=params).json()
    assert next(row["plays"] for row in top if row["metadata"]["track_id"] == key) == 1
    exported = client.get(
        "/api/export", params={**params, "view": "tracks", "format": "json"}
    ).json()
    assert next(row["plays"] for row in exported["items"] if row["track_id"] == key) == 1
    assert (
        client.get(f"/api/catalog/{key}/radio", params={"until": now.isoformat()}).json()[
            "detections"
        ]
        == 201
    )
    for invalid in (
        {"station_ids": "nope"},
        {"since": now.isoformat(), "until": now.isoformat()},
        {"exclusions": "[broken"},
        {"since": "invalid"},
    ):
        assert client.get(f"/api/catalog/{key}/radio", params=invalid).status_code == 422


def test_unknown_legacy_identities_are_not_merged(client, db, ranking_history):
    db.execute(update(Play).values(track_id=None))
    db.commit()
    data = client.get("/api/stats/top").json()
    # Keep the three raw case variants distinct without a confirmed common identity.
    assert len(data) == 6


def test_personal_hiding_filters_all_case_variants_in_detail(client, ranking_history):
    _, _, key = ranking_history
    scope = {"exclusions": json.dumps([{"artist": "ESTL", "title": "TE RENCONTRER ENCORE"}])}
    assert client.get(f"/api/catalog/{key}/radio", params=scope).json()["detections"] == 0
    top = client.get("/api/stats/top", params=scope).json()
    assert all(row["metadata"]["track_id"] != key for row in top)
