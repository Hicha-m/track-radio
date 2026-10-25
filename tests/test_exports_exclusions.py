import csv
import io
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.models import Play, Station


@pytest.fixture
def songs(db):
    station = Station(name="Radio", slug="radio", stream_url="https://example.com/live")
    db.add(station)
    db.flush()
    for i, (artist, title) in enumerate(
        [
            (None, "Mouv’"),
            ("Hit Music Only !", "NRJ"),
            ("SKYROCK", "Les Dédicaces"),
            ("Mosimann", "Soon"),
            ("Other", "=SUM(1+1)"),
        ]
    ):
        now = datetime.now(UTC) - timedelta(minutes=i)
        db.add(
            Play(
                station_id=station.id,
                artist=artist,
                title=title,
                source="icy",
                raw_metadata=title,
                detected_at=now,
                last_seen_at=now,
            )
        )
    db.commit()
    return station


def hidden():
    return json.dumps(
        [
            {"artist": None, "title": "Mouv’"},
            {"artist": "HIT MUSIC ONLY !", "title": "nrj"},
            {"artist": "skyrock", "title": "Les Dedicaces"},
        ]
    )


def test_exclusions_apply_before_pagination_and_ranking(client, songs):
    result = client.get("/api/explore", params={"exclusions": hidden(), "page_size": 1}).json()
    assert result["items"][0]["title"] == "Soon"
    assert result["has_more"]
    result = client.get("/api/stats/top", params={"exclusions": hidden()}).json()
    assert {t["title"] for t in result} == {"Soon", "=SUM(1+1)"}
    # Another user's unfiltered cache must remain independent.
    assert len(client.get("/api/stats/top").json()) == 5


def test_export_uses_filters_and_includes_all_pages(client, songs):
    response = client.get(
        "/api/export",
        params={
            "format": "json",
            "view": "tracks",
            "exclusions": hidden(),
            "page": 2,
            "page_size": 1,
        },
    )
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    data = response.json()
    assert data["count"] == 2
    assert len(data["items"]) == 2
    assert all(
        t["spotify_url"].startswith("https://open.spotify.com/search/") for t in data["items"]
    )
    response = client.get(
        "/api/export", params={"format": "json", "artist": "mosimann", "exact": True}
    )
    assert [t["title"] for t in response.json()["items"]] == ["Soon"]


def test_csv_escapes_formulas_and_preserves_accents(client, songs):
    response = client.get("/api/export", params={"format": "csv", "view": "tracks"})
    assert response.status_code == 200
    records = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"))
    assert any(t["title"] == "Les Dédicaces" for t in records)
    assert any(t["title"] == "'=SUM(1+1)" for t in records)


def test_export_limit_is_explicit_and_invalid_rules_rejected(client, songs, monkeypatch):
    monkeypatch.setattr("app.export_api.MAX_EXPORT_ROWS", 2)
    assert client.get("/api/export", params={"format": "json"}).status_code == 422
    for route in ("/api/explore", "/api/stats/top", "/api/export"):
        assert client.get(route, params={"exclusions": "{wrong json"}).status_code == 422
        assert (
            client.get(route, params={"exclusions": json.dumps([{"title": "a"}] * 51)}).status_code
            == 422
        )


@pytest.mark.parametrize("fmt", ["csv", "json"])
@pytest.mark.parametrize("admin", [False, True])
def test_custom_top_ten_filters_before_limit(db, client, fmt, admin):
    station = Station(name="Target", slug="target", stream_url="https://example.com/live")
    other = Station(name="Other", slug="other", stream_url="https://example.com/other")
    db.add_all([station, other])
    db.flush()
    at = datetime.now(UTC)
    for i in range(14):
        for j in range(15 - i):
            db.add(
                Play(
                    station_id=station.id,
                    artist="Artist",
                    title=f"Song {i:02}",
                    source="icy",
                    raw_metadata="raw",
                    detected_at=at - timedelta(minutes=j),
                    last_seen_at=at,
                )
            )
    for _ in range(20):
        db.add(
            Play(
                station_id=other.id,
                artist="Other",
                title="Outside selection",
                source="icy",
                raw_metadata="raw",
                detected_at=at,
                last_seen_at=at,
            )
        )
    db.commit()
    assert (
        client.post(
            "/api/admin/exclusions",
            json={"artist": "Artist", "title": "Song 00"},
            headers={"Authorization": "Bearer test-admin-token"},
        ).status_code
        == 200
    )
    params = {
        "format": fmt,
        "view": "tracks",
        "station_id": station.id,
        "sort": "frequency",
        "limit": 10,
        "fields": "title,artist",
        "exclusions": json.dumps([{"artist": "Artist", "title": "Song 01"}]),
        "since": (at - timedelta(hours=1)).isoformat(),
        "artist": "Artist",
    }
    headers = {"Authorization": "Bearer test-admin-token"} if admin else {}
    response = client.get("/api/export", params=params, headers=headers)
    assert response.status_code == 200, response.text
    if fmt == "json":
        assert response.json()["count"] == 10
        rows = response.json()["items"]
    else:
        rows = list(
            csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";")
        )
    assert [r["title"] for r in rows] == [f"Song {i:02}" for i in range(2, 12)]
    assert all(list(r) == ["title", "artist"] for r in rows)


def test_complete_export_and_nested_csv_roundtrip(db, client, songs):
    from app.services.catalog import ensure_entry

    entry = ensure_entry(db, "Mosimann", "Soon")
    entry.status = "matched"
    entry.selected = {
        "provider": "musicbrainz",
        "provider_id": "42",
        "album": "Album",
        "genres": ["Pop", "Dance"],
        "isrc": "FR123",
        "release_id": "release",
    }
    entry.lyrics = {"plain": "Paroles\navec accents", "provider": "lrclib"}
    db.commit()
    params = {"artist": "Mosimann", "format": "json"}
    row = client.get("/api/export", params=params).json()["items"][0]
    assert row["album"] == "Album"
    assert row["genres"] == ["Pop", "Dance"]
    assert row["metadata"]["release_id"] == "release"
    assert row["lyrics"] == entry.lyrics
    params.update(format="csv", fields="title,genres,lyrics,metadata")
    response = client.get("/api/export", params=params)
    csv_row = next(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"))
    for key in ("genres", "lyrics", "metadata"):
        assert json.loads(csv_row[key]) == row[key]


@pytest.mark.parametrize(
    "params",
    [
        {"fields": ""},
        {"fields": "title,title"},
        {"fields": "secret"},
        {"view": "tracks", "fields": "station_name"},
        {"limit": 0},
        {"limit": -1},
        {"limit": 10001},
        {"limit": "invalid"},
    ],
)
def test_invalid_custom_export(client, params):
    assert client.get("/api/export", params=params).status_code == 422


def test_empty_custom_csv_has_only_requested_headers(client):
    response = client.get("/api/export", params={"fields": "title,artist"})
    assert response.content.decode("utf-8-sig") == "title;artist\r\n"
