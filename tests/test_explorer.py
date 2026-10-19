from datetime import UTC, datetime, timedelta

import pytest

from app.models import Play, Station


@pytest.fixture
def history(db):
    stations = [
        Station(name="Radio Alpha", slug="alpha", stream_url="https://example.com"),
        Station(name="Radio Beta", slug="beta", stream_url="https://example.com", enabled=False),
    ]
    db.add_all(stations)
    db.commit()
    date = datetime(2026, 10, 1, 12, tzinfo=UTC)
    rows = []
    for i, (station, artist, title, source) in enumerate(
        [
            (0, "ÉTOILE", "L'été", "icy"),
            (0, "ÉTOILE", "L'été", "json"),
            (1, "ÉTOILE", "L'été", "html"),
            (0, "Other", "100%_Live", "icy"),
            (1, None, "Unknown", "json"),
        ]
    ):
        rows.append(
            Play(
                station_id=stations[station].id,
                artist=artist,
                title=title,
                source=source,
                raw_metadata=f"raw {title}",
                detected_at=date + timedelta(hours=i),
                last_seen_at=date + timedelta(hours=i, minutes=5),
            )
        )
    db.add_all(rows)
    db.commit()
    return stations, rows


def test_explorer_filters_combine_and_unicode_matches(client, history):
    stations, rows = history
    response = client.get(
        "/api/explore",
        params={
            "station_id": stations[0].id,
            "artist": "etoile",
            "title": "ete",
            "source": "json",
            "enabled": "true",
            "since": "2026-10-01T12:30:00Z",
            "until": "2026-10-01T14:00:00Z",
            "seen_since": "2026-10-01T13:01:00Z",
            "raw": "ete",
        },
    )
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["items"]] == [rows[1].id]
    assert response.json()["items"][0]["detected_at"].endswith("+00:00")


def test_tracks_group_across_stations_and_drill_down(client, history):
    data = client.get("/api/explore", params={"view": "tracks", "min_plays": 2}).json()
    assert len(data["items"]) == 1
    assert data["items"][0]["plays"] == 3
    assert data["items"][0]["stations"] == 2
    data = client.get(
        "/api/explore", params={"artist": "etoile", "title": "L'ete", "exact": "true"}
    ).json()
    assert len(data["items"]) == 3


def test_dates_with_offsets_are_compared_in_utc(client, history):
    data = client.get(
        "/api/explore",
        params={
            "since": "2026-10-01T15:00:00+02:00",
            "until": "2026-10-01T16:00:00+02:00",
        },
    ).json()
    assert len(data["items"]) == 1
    assert data["items"][0]["source"] == "json"


def test_literal_wildcards_and_missing_artist(client, history):
    assert len(client.get("/api/explore?q=%25_").json()["items"]) == 1
    data = client.get("/api/explore", params={"artist_status": "missing"}).json()
    assert [row["title"] for row in data["items"]] == ["Unknown"]
    data = client.get("/api/explore", params={"q": "Beta", "artist_status": "known"}).json()
    assert [row["title"] for row in data["items"]] == ["L'été"]


def test_pagination_is_stable_when_new_play_arrives(client, history, db):
    stations, rows = history
    first = client.get("/api/explore?page_size=2").json()
    assert first["has_more"]
    db.add(
        Play(
            station_id=stations[0].id,
            artist="New",
            title="New",
            source="icy",
            raw_metadata="New",
            detected_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
    )
    db.commit()
    second = client.get(
        "/api/explore", params={"page_size": 2, "page": 2, "snapshot_id": first["snapshot_id"]}
    ).json()
    assert [item["id"] for item in second["items"]] == [rows[2].id, rows[1].id]
    assert not {item["id"] for item in first["items"]} & {item["id"] for item in second["items"]}


@pytest.mark.parametrize(
    "params",
    [
        {"page_size": 1000},
        {"page": 0},
        {"page": 10**30},
        {"source": "sql"},
        {"sort": "title; DROP TABLE plays"},
        {"since": "2026-10-02", "until": "2026-10-01"},
    ],
)
def test_reject_invalid_exploration(client, params):
    assert client.get("/api/explore", params=params).status_code == 422


def test_text_search_sorts_by_actual_date_for_backfilled_plays(client, history, db):
    stations, rows = history
    old = Play(
        station_id=stations[0].id,
        artist="ÉTOILE",
        title="Backfilled",
        source="icy",
        raw_metadata="Backfilled",
        detected_at=datetime(2025, 1, 1, tzinfo=UTC),
        last_seen_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    db.add(old)
    db.commit()
    items = client.get("/api/explore", params={"artist": "etoile"}).json()["items"]
    assert [item["id"] for item in items] == [rows[2].id, rows[1].id, rows[0].id, old.id]


def test_search_has_dedicated_page_and_preserves_explorer(client):
    home = client.get("/").text
    assert 'id="chart-filters"' in home
    assert 'id="chart-list"' in home
    assert 'id="explorer-form"' not in home
    assert 'href="/recherche"' in home
    html = client.get("/recherche?view=tracks&artist=Test").text
    assert 'id="explorer-form"' in html
    assert 'id="chart-filters"' not in html
    assert 'name="artist"' in html
    assert 'name="since"' in html
    assert 'id="show-add"' not in html
    assert 'id="add-dialog"' not in html
    assert 'name="hours"' not in html


def test_quick_search_combines_words_across_artist_and_title(client, history):
    data = client.get("/api/explore", params={"view": "tracks", "q": "ete etoile"}).json()
    assert len(data["items"]) == 1
    assert data["items"][0]["artist"] == "ÉTOILE"
    assert data["items"][0]["plays"] == 3
    assert client.get("/api/explore", params={"q": "etoile missing"}).json()["items"] == []
