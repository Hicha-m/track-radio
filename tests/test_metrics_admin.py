import json
from datetime import UTC, datetime, timedelta

import pytest

from app.metrics_api import MetricsFilters, compute, invalidate_metrics
from app.models import Play, Station
from app.services.catalog import ensure_entry, identity
from app.services.station_config import StationConfig, apply_stations

AUTH = {"Authorization": "Bearer test-admin-token"}
NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)


def station(db, name):
    result = Station(name=name, slug=name.lower(), stream_url="https://example.com/live")
    db.add(result)
    db.flush()
    return result


def play(db, radio, title="Song", artist="Artist", at=NOW):
    result = Play(
        station_id=radio.id,
        title=title,
        artist=artist,
        detected_at=at,
        last_seen_at=at,
        source="icy",
        raw_metadata=title,
    )
    db.add(result)
    db.flush()
    return result


def test_repetitions_normalized_identity_average_and_exclusions(db):
    radio = station(db, "A")
    for i, artist in enumerate(["Ártist", "ARTIST", "Artist"]):
        play(db, radio, artist=artist, at=NOW - timedelta(hours=i + 1))
    play(db, radio, title="Song (Live)", at=NOW - timedelta(hours=1))
    play(db, radio, at=NOW - timedelta(hours=13))
    data = compute(db, "repetitions", MetricsFilters(), NOW)
    assert len(data["items"]) == 1
    assert data["items"][0]["plays"] == 3
    assert data["items"][0]["average_seconds"] == 3600
    hidden = json.dumps([{"artist": "Artist", "title": "Song"}])
    assert not compute(db, "repetitions", MetricsFilters(exclusions=hidden), NOW)["items"]


def test_summary_uses_full_sets_and_separate_windows_before_aggregation(db):
    a, b = station(db, "A"), station(db, "B")
    for radio in (a, b):
        play(db, radio, at=NOW - timedelta(hours=1))
    play(db, a, title="Other", at=NOW - timedelta(hours=20))
    play(db, a, title="Old", at=NOW - timedelta(hours=26))
    entry = ensure_entry(db, "Artist", "Song")
    entry.primary_genre = "Pop"
    db.commit()
    data = compute(db, "summary", MetricsFilters(page_size=1), NOW)
    assert data["total"] == 3 and data["unique_tracks"] == 2
    assert data["stations"] == 2 and data["known_genres"] == 2
    assert data["genre_coverage"] == 66.7
    assert data["top"]["title"] == "Song" and data["top"]["plays"] == 2
    assert data["top_since"] == NOW - timedelta(hours=12)
    filtered = compute(
        db,
        "summary",
        MetricsFilters(
            station_id=a.id, exclusions=json.dumps([{"artist": "Artist", "title": "Song"}])
        ),
        NOW,
    )
    assert filtered["total"] == 1 and filtered["unique_tracks"] == 1
    assert filtered["genre_coverage"] == 0 and filtered["top"] is None
    day = compute(db, "summary", MetricsFilters(hours=24, station_id=a.id), NOW)
    assert day["top"] is not None and day["top_since"] == NOW - timedelta(hours=24)


def test_summary_empty_and_api_exclusion_cache(client, db):
    data = client.get("/api/stats/summary").json()
    assert data["total"] == data["unique_tracks"] == data["stations"] == 0
    assert data["top"] is None and data["genre_coverage"] == 0


def test_diversity_overlap_empty_radios_and_pagination(db):
    a, b, empty = [station(db, name) for name in ["A", "B", "C"]]
    for radio, titles in [(a, ["One", "One", "Two"]), (b, ["One", "Three"])]:
        for title in titles:
            play(db, radio, title=title, at=NOW - timedelta(hours=1))
    data = compute(db, "diversity", MetricsFilters(page_size=1), NOW)
    assert data["has_more"]
    assert data["items"][0]["unique_tracks"] == 2
    assert data["items"][0]["plays"] == 3
    matrix = compute(db, "overlap", MetricsFilters(), NOW)["items"]
    pair = next(r for r in matrix if r["station_a"] == a.id and r["station_b"] == b.id)
    assert pair == {"station_a": a.id, "station_b": b.id, "shared": 1, "union": 3, "percent": 33.3}
    assert next(r for r in matrix if r["station_a"] == empty.id)["percent"] is None


def test_offsets_nearest_unique_matching_and_pagination(db):
    a, b = station(db, "A"), station(db, "B")
    start = NOW - timedelta(hours=1)
    left = play(db, a, at=start)
    play(db, a, at=start + timedelta(seconds=100))
    nearest = play(db, b, at=start + timedelta(seconds=10))
    play(db, b, at=start + timedelta(seconds=180))
    play(db, b, title="Song (Live)", at=start)
    data = compute(db, "offsets", MetricsFilters(station_a=a.id, station_b=b.id, page_size=1), NOW)
    assert data["has_more"]
    assert data["items"][0]["id_a"] == left.id
    assert data["items"][0]["id_b"] == nearest.id
    assert data["items"][0]["seconds"] == 10
    second = compute(
        db, "offsets", MetricsFilters(station_a=a.id, station_b=b.id, page=2, page_size=1), NOW
    )
    assert second["items"][0]["seconds"] == 80
    assert not second["has_more"]
    with pytest.raises(Exception, match="différentes"):
        compute(db, "offsets", MetricsFilters(station_a=a.id, station_b=a.id), NOW)


def test_hourly_browser_timezone_includes_both_dst_hours(db):
    radio = station(db, "A")
    for hour in [0, 1]:
        play(db, radio, at=datetime(2026, 10, 25, hour, 15, tzinfo=UTC))
    result = compute(
        db,
        "hourly",
        MetricsFilters(track_id=identity("Artist", "Song"), timezone="Europe/Paris"),
        NOW,
    )
    assert len(result["items"]) == 24
    assert result["items"][2]["plays"] == 2
    assert sum(r["plays"] for r in result["items"]) == 2
    with pytest.raises(Exception, match="Fuseau"):
        compute(
            db,
            "hourly",
            MetricsFilters(track_id=identity("Artist", "Song"), timezone="not/a/timezone"),
            NOW,
        )


def test_genres_filter_before_grouping_pagination_and_export(db, client):
    radio = station(db, "A")
    recent = datetime.now(UTC) - timedelta(minutes=10)
    for title in ["Rock Song", "Unknown Song", "Rock Song"]:
        play(db, radio, title=title, at=recent)
    entry = ensure_entry(db, "Artist", "Rock Song")
    entry.primary_genre = "Rock"
    entry.selected = {
        "provider": "itunes",
        "provider_id": "42",
        "genres": ["Rock"],
        "genre_source": "itunes",
    }
    db.commit()
    invalidate_metrics()
    result = client.get("/api/stats/genres").json()
    assert result["coverage"] == 66.7
    assert {r["genre"]: r["plays"] for r in result["items"]} == {"Rock": 2, "Inconnu": 1}
    result = client.get("/api/explore?view=tracks&genre=Rock&page_size=1").json()
    assert len(result["items"]) == 1 and result["items"][0]["plays"] == 2
    assert not result["has_more"]
    assert (
        client.get("/api/explore?genre=__unknown__").json()["items"][0]["title"] == "Unknown Song"
    )
    exported = client.get("/api/export?genre=Rock&format=json").json()
    assert exported["count"] == 2
    assert all(r["primary_genre"] == "Rock" for r in exported["items"])


def test_administration_authentication_public_privacy_and_station_overrides(db, client):
    radio = station(db, "A")
    entry = ensure_entry(db, "Artist", "Song")
    entry.candidates = [{"artist": "Wrong Artist", "title": "Wrong Song"}]
    db.commit()
    for path in [
        "/api/admin/access",
        "/api/admin/stations",
        "/api/admin/catalog",
        f"/api/admin/catalog/{entry.id}",
        "/api/catalog/spotify-match?title=Song",
    ]:
        response = client.get(path)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
    assert (
        client.patch(f"/api/admin/stations/{radio.id}", json={"enabled": False}).status_code == 401
    )
    public = client.get("/api/catalog?artist=Artist&title=Song").json()
    assert "candidates" not in public and "attempts" not in public
    assert "Wrong Artist" not in str(public)
    response = client.patch(
        f"/api/admin/stations/{radio.id}", json={"enabled": False}, headers=AUTH
    )
    assert response.status_code == 200 and not response.json()["enabled"]
    values = [StationConfig(slug="a", name="Radio A", stream_url="https://example.com/live")]
    apply_stations(db, values)
    assert not radio.enabled and radio.config_enabled
    assert client.patch(
        f"/api/admin/stations/{radio.id}", json={"enabled": None}, headers=AUTH
    ).json()["enabled"]
    apply_stations(db, [])
    assert (
        client.patch(
            f"/api/admin/stations/{radio.id}", json={"enabled": True}, headers=AUTH
        ).status_code
        == 409
    )
    assert not radio.enabled
    assert client.get(f"/api/admin/catalog/{entry.id}", headers=AUTH).json()["candidates"]
    for route in ["/stats", "/admin", "/recherche"]:
        assert client.get(route).status_code == 200
    assert client.get("/api/stats/top").status_code == 200


def test_metrics_query_windows_and_pagination_validate(client):
    for kind in ("repetitions", "diversity", "overlap", "genres"):
        response = client.get(f"/api/stats/{kind}?hours=12&page=1&page_size=25")
        assert response.status_code == 200, response.text
    assert client.get("/api/stats/repetitions?hours=7").status_code == 422
    assert client.get("/api/stats/repetitions?page_size=101").status_code == 422


def test_masked_station_labels_curly_apostrophes_and_stale_identity_removed_from_diversity(
    db, client
):
    from sqlalchemy import update

    radio = station(db, "Mouv")
    now = datetime.now(UTC) - timedelta(minutes=10)
    rows = [play(db, radio, title="Mouv'", artist=None, at=now) for _ in range(3)]
    play(db, radio, title="Real music", at=now)
    db.commit()
    # An older/stale identity must not bypass the text exclusion.
    db.execute(update(Play).where(Play.id == rows[0].id).values(track_id="0" * 64))
    db.commit()
    before = client.get("/api/stats/diversity").json()["items"][0]
    hidden = json.dumps([{"artist": None, "title": "Mouv’"}])
    params = {"exclusions": hidden}
    after = client.get("/api/stats/diversity", params=params).json()["items"][0]
    assert before["plays"] == 4
    assert after["plays"] == 1 and after["unique_tracks"] == 1
    assert not client.get("/api/stats/repetitions", params=params).json()["items"]
    assert client.get("/api/stats/genres", params=params).json()["total"] == 1
    assert (
        client.get("/api/stats/diversity", params=params).json()
        == client.get("/api/stats/diversity", params=params).json()
    )


def test_radio_summary_preserves_close_detections_and_uses_only_local_data(db, client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No provider calls are allowed when reading a track.")

    monkeypatch.setattr("app.catalog_api.fetch_json", forbidden)
    a, b = station(db, "A"), station(db, "B")
    now = datetime.now(UTC) - timedelta(minutes=10)
    play(db, a, at=now)
    play(db, a, at=now + timedelta(seconds=42))
    play(db, b, at=now - timedelta(days=1))
    db.commit()
    key = identity("Artist", "Song")
    result = client.get(f"/api/catalog/{key}/radio").json()
    assert result["detections"] == 3 and result["stations"] == 2
    assert len(result["recent"]) == 3
    assert result["recent"][0]["station_name"] == "A"
    assert datetime.fromisoformat(result["recent"][0]["detected_at"]).utcoffset() == timedelta(0)
    assert client.get(f"/api/catalog/{key}/radio?station_id={a.id}").json()["detections"] == 2
    assert client.get("/api/catalog?artist=Artist&title=Song").status_code == 200
    assert client.get("/api/catalog/not-a-valid-key/radio").status_code == 422


def test_review_queue_orders_doubtful_then_most_played(db, client):
    radio = station(db, "A")
    now = datetime.now(UTC) - timedelta(minutes=10)
    doubtful = ensure_entry(db, "Artist", "Doubtful")
    doubtful.status = "ambiguous"
    popular = ensure_entry(db, "Artist", "Popular")
    popular.status = "not_found"
    obscure = ensure_entry(db, "Artist", "Obscure")
    obscure.status = "not_found"
    for title, count in [("Doubtful", 1), ("Popular", 4), ("Obscure", 0)]:
        for _ in range(count):
            play(db, radio, title=title, at=now)
    db.commit()
    result = client.get("/api/admin/catalog?review=true", headers=AUTH).json()["items"]
    assert [r["title"] for r in result] == ["Doubtful", "Popular", "Obscure"]
    assert [r["recent_plays"] for r in result] == [1, 4, 0]
    assert "Administration" not in client.get("/").text.split("</nav>")[0]
    assert client.get("/admin/catalogue").status_code == 200
