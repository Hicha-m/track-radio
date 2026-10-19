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
