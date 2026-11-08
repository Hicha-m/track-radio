"""Playlist preparation uses filtered identities; exports never write to accounts."""

import csv
import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models import GlobalExclusion, Play, Station, YouTubeMatch
from app.services.catalog import ensure_entry
from app.services.exclusions import exclusion_id, normalized_pair


@pytest.fixture
def history(db):
    now = datetime.now(UTC)
    stations = [
        Station(name=f"Radio {i}", slug=f"r{i}", stream_url="https://example.test/live")
        for i in range(2)
    ]
    db.add_all(stations)
    db.flush()
    keys = []
    for index, (title, count, station) in enumerate(
        [
            ("Song", 3, 0),
            ("SONG", 2, 1),
            ("Song (Live)", 2, 0),
            ("Other", 1, 1),
            ("=FORMULA()", 1, 0),
        ]
    ):
        track = ensure_entry(db, "Artist", title)
        keys.append(track.id)
        for number in range(count):
            db.add(
                Play(
                    station_id=stations[station].id,
                    artist="Artist",
                    title=title,
                    source="json",
                    detected_at=now - timedelta(days=index, seconds=number),
                    last_seen_at=now,
                    raw_metadata="DO NOT EXPORT",
                )
            )
    candidate = {"id": "aaaaaaaaaaa", "title": "Artist Song", "channel": "Artist"}
    db.add(
        YouTubeMatch(
            track_id=keys[0],
            artist="Artist",
            title="Song",
            requested_at=now,
            status="matched",
            candidates=[candidate],
            selected=candidate,
        )
    )
    db.commit()
    return now, stations, keys


def test_selection_identity_order_period_and_station(client, db, history):
    now, stations, keys = history
    response = client.get("/api/playlists/selection", params={"limit": 3})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    rows = response.json()["items"]
    assert [row["plays"] for row in rows] == [5, 2, 1]
    assert rows[0]["track_id"] == keys[0] == keys[1]
    assert rows[1]["track_id"] != keys[0]
    assert set(rows[0]) == {"track_id", "artist", "title", "plays"}
    assert "DO NOT EXPORT" not in response.text
    assert (
        client.get("/api/playlists/selection", params={"station_ids": str(stations[1].id)}).json()[
            "items"
        ][0]["plays"]
        == 2
    )
    assert (
        client.get(
            "/api/playlists/selection", params={"since": (now - timedelta(hours=1)).isoformat()}
        ).json()["items"][0]["plays"]
        == 3
    )
    alphabetical = client.get("/api/playlists/selection", params={"sort": "title"}).json()["items"]
    assert [r["title"] for r in alphabetical] == sorted(r["title"] for r in alphabetical)
    assert db.scalar(select(func.count(Play.id))) == 9


@pytest.mark.parametrize("admin", [False, True])
def test_personal_and_global_exclusions_before_limit_and_export(client, db, history, admin):
    _, _, keys = history
    a, t = normalized_pair("Artist", "Song")
    db.add(
        GlobalExclusion(
            id=exclusion_id(a, t), artist=a, title=t, artist_normalized=a, title_normalized=t
        )
    )
    db.commit()
    hidden = json.dumps([{"artist": "Artist", "title": "Song (Live)"}])
    headers = {"Authorization": "Bearer test-admin-token"} if admin else {}
    response = client.get(
        "/api/playlists/selection", params={"exclusions": hidden, "limit": 1}, headers=headers
    )
    assert response.json()["items"][0]["title"] == "=FORMULA()"
    result = client.post(
        "/api/playlists/export",
        params={"exclusions": hidden},
        headers=headers,
        json={"items": [{"track_id": key} for key in keys]},
    ).json()
    assert [row["title"] for row in result["items"]] == ["Other", "=FORMULA()"]
    assert result["skipped"] == 3
    assert db.scalar(select(func.count(Play.id))) == 9


def test_export_order_duplicates_video_validation_and_csv_safety(client, history):
    _, _, keys = history
    items = [
        {"track_id": keys[3]},
        {"track_id": keys[0], "video_id": "aaaaaaaaaaa"},
        {"track_id": keys[1]},
        {"track_id": "f" * 64},
        {"track_id": keys[4]},
    ]
    response = client.post("/api/playlists/export", json={"items": items})
    assert response.status_code == 200
    result = response.json()
    assert result["skipped"] == 2
    assert [r["title"] for r in result["items"]] == ["Other", "SONG", "=FORMULA()"]
    assert result["items"][1]["url"] == "https://www.youtube.com/watch?v=aaaaaaaaaaa"
    assert result["items"][1]["status"] == "ready"
    assert result["items"][0]["url"].startswith("https://www.youtube.com/results?")
    tampered = client.post(
        "/api/playlists/export", json={"items": [{"track_id": keys[0], "video_id": "bbbbbbbbbbb"}]}
    ).json()
    assert tampered["items"][0]["video"] is None
    csv_response = client.post("/api/playlists/export", json={"items": items, "format": "csv"})
    rows = list(
        csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig")), delimiter=";")
    )
    assert rows[2]["title"] == "'=FORMULA()"
    assert csv_response.headers["cache-control"] == "no-store"
    assert "attachment" in csv_response.headers["content-disposition"]


def test_spotify_exact_links_and_search_fallback(client, db, history):
    _, _, keys = history
    entry = ensure_entry(db, "Artist", "Song")
    entry.selected = {
        "provider": "spotify",
        "provider_id": "A" * 22,
        "url": "https://open.spotify.com/track/" + "A" * 22,
        "artist": "Artist",
        "title": "Song",
    }
    entry.status = "matched"
    db.commit()
    response = client.post(
        "/api/playlists/export",
        json={"items": [{"track_id": keys[0]}, {"track_id": keys[3]}], "destination": "spotify"},
    ).json()
    assert response["items"][0]["url"] == entry.selected["url"]
    assert response["items"][0]["status"] == "ready"
    assert response["items"][1]["url"].startswith("https://open.spotify.com/search/")
    assert response["items"][1]["status"] == "unresolved"
    entry.selected = {**entry.selected, "url": "https://example.test/not-spotify"}
    db.commit()
    result = client.post(
        "/api/playlists/export", json={"items": [{"track_id": keys[0]}], "destination": "spotify"}
    ).json()
    assert result["items"][0]["status"] == "unresolved"


def test_rejected_or_changed_video_does_not_export(client, db, history):
    _, _, keys = history
    match = db.get(YouTubeMatch, keys[0])
    match.status = "rejected"
    db.commit()
    result = client.post(
        "/api/playlists/export", json={"items": [{"track_id": keys[0], "video_id": "aaaaaaaaaaa"}]}
    ).json()
    assert result["items"][0]["video"] is None


@pytest.mark.parametrize(
    "query",
    [
        {"limit": 0},
        {"limit": 51},
        {"station_ids": "bad"},
        {"since": "2026-02-02", "until": "2026-01-01"},
        {"exclusions": "invalid"},
        {"sort": "bad"},
    ],
)
def test_invalid_selection(client, query):
    assert client.get("/api/playlists/selection", params=query).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"items": []},
        {"items": [{"track_id": "bad"}]},
        {"items": [{"track_id": "f" * 64, "video_id": "https://bad"}]},
        {"items": [{"track_id": "f" * 64}] * 51},
        {"items": [{"track_id": "f" * 64}], "destination": "bad"},
    ],
)
def test_invalid_export(client, body):
    assert client.post("/api/playlists/export", json=body).status_code == 422


def test_video_duplicates_and_new_global_exclusion_are_rechecked(client, db, history):
    now, _, keys = history
    candidate = {"id": "aaaaaaaaaaa", "title": "Other", "channel": "Artist"}
    db.add(
        YouTubeMatch(
            track_id=keys[3],
            artist="Artist",
            title="Other",
            status="matched",
            candidates=[candidate],
            selected=candidate,
            requested_at=now,
        )
    )
    db.commit()
    items = [
        {"track_id": keys[0], "video_id": "aaaaaaaaaaa"},
        {"track_id": keys[3], "video_id": "aaaaaaaaaaa"},
    ]
    data = client.post("/api/playlists/export", json={"items": items}).json()
    assert [row["status"] for row in data["items"]] == ["ready", "unresolved"]
    # A playlist prepared earlier must not bypass a newly created exclusion.
    client.get("/api/playlists/selection")
    a, t = normalized_pair("Artist", "Song")
    db.add(
        GlobalExclusion(
            id=exclusion_id(a, t), artist=a, title=t, artist_normalized=a, title_normalized=t
        )
    )
    db.commit()
    result = client.post("/api/playlists/export", json={"items": items})
    assert result.headers["x-playlist-skipped"] == "1"
    assert [row["title"] for row in result.json()["items"]] == ["Other"]
