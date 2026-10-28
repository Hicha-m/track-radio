import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import GlobalExclusion, Play, Station, TrackMetadata
from app.services.collector import record_metadata
from app.services.exclusions import exclusion_id, normalized_pair
from app.services.metadata import TrackMetadata as Observation
from app.services.track_identity import identity

AUTH = {"Authorization": "Bearer test-admin-token"}
NOISE = {"artist": "SKYROCK", "title": "100% Francais"}


def exclude(db, track=NOISE):
    a, t = normalized_pair(**track)
    db.add(
        GlobalExclusion(id=exclusion_id(**track), **track, artist_normalized=a, title_normalized=t)
    )
    db.commit()


def radio(db, name="Test"):
    station = Station(name=name, slug=name.lower(), stream_url="https://example.test/live")
    db.add(station)
    db.commit()
    return station


def observe(db, station, title="100% Francais", artist="SKYROCK", start=None):
    return record_metadata(db, station, Observation(artist, title, title, "icy", start))


def test_blacklisted_broadcasts_skip_inserts_and_keep_transition_after_restart(db):
    station = radio(db)
    exclude(db)
    first, _ = observe(db, station, title="Song", artist="Artist")
    assert observe(db, station) == (None, False)
    with Session(db.get_bind()) as restarted:
        assert observe(restarted, station) == (None, False)
        second, created = observe(restarted, station, title="Song", artist="Artist")
        assert created and second.id != first.id
        assert not observe(restarted, station, title="Song", artist="Artist")[1]
    assert db.query(Play).count() == 2
    assert db.query(Play).filter_by(title=NOISE["title"]).count() == 0


def test_existing_hidden_history_is_not_updated_and_restoration_resumes_collection(db):
    station = radio(db)
    historical, _ = observe(db, station)
    at = historical.last_seen_at
    exclude(db)
    assert observe(db, station) == (None, False)
    assert historical.last_seen_at == at
    assert db.query(Play).count() == 1
    db.delete(db.get(GlobalExclusion, exclusion_id(**NOISE)))
    db.commit()
    _, created = observe(db, station)
    assert created
    assert db.query(Play).count() == 2


def test_blacklisted_dated_event_rejects_stale_response_after_restart(db):
    station = radio(db)
    exclude(db)
    observe(db, station, title="Song", start=1000)
    observe(db, station, start=2000)
    with Session(db.get_bind()) as restarted:
        assert observe(restarted, station, title="Song", start=1000) == (None, False)
        assert observe(restarted, station, title="Song", start=3000)[1]
    assert db.query(Play).count() == 2


def test_worker_does_not_enrich_blacklisted_tracks(db, monkeypatch):
    from app.worker import save_metadata

    class Borrow:
        def __enter__(self):
            return db

        def __exit__(self, *args):
            pass

    station = radio(db)
    exclude(db)
    monkeypatch.setattr("app.worker.SessionLocal", Borrow)
    monkeypatch.setattr(
        "app.worker.ensure_entry", lambda *args: pytest.fail("Must not enrich noise")
    )
    assert not save_metadata(station.id, Observation(**NOISE, raw="noise", source="icy"))
    assert db.query(Play).count() == 0
    assert db.query(TrackMetadata).count() == 0


def populate(db):
    a, b = radio(db, "A"), radio(db, "B")
    start = datetime.now(UTC) - timedelta(hours=1)
    for station in (a, b):
        for track in (NOISE, {"artist": "Artist", "title": "Music"}):
            key = identity(**track)
            if db.get(TrackMetadata, key) is None:
                db.add(TrackMetadata(id=key, **track, primary_genre="Pop"))
            for seconds in (0, 120, 240):
                at = start + timedelta(seconds=seconds + station.id)
                db.add(
                    Play(
                        station_id=station.id,
                        **track,
                        raw_metadata="original",
                        source="icy",
                        detected_at=at,
                        last_seen_at=at,
                    )
                )
    db.commit()
    return a, b


@pytest.mark.parametrize("mode", ["personal", "global"])
def test_noise_excluded_before_all_statistics_and_exports(db, client, mode):
    a, b = populate(db)
    key = identity(**NOISE)
    # Warm caches before changing filters.
    assert client.get("/api/stats/genres").json()["total"] == 12
    assert client.get("/api/stats/summary").json()["total"] == 12
    assert len(client.get("/api/stats/repetitions").json()["items"]) == 4
    params = {}
    if mode == "global":
        assert client.post("/api/admin/exclusions", json=NOISE, headers=AUTH).status_code == 200
    else:
        params["exclusions"] = json.dumps([NOISE])
    for auth in ({}, AUTH):

        def stats(kind, auth=auth, **extra):
            response = client.get(f"/api/stats/{kind}", params={**params, **extra}, headers=auth)
            assert response.status_code == 200, response.text
            return response.json()

        repeated = stats("repetitions")["items"]
        assert len(repeated) == 2 and all(t["title"] == "Music" for t in repeated)
        assert all(r["average_seconds"] == 120 for r in repeated)
        assert all(r["plays"] == 3 and r["unique_tracks"] == 1 for r in stats("diversity")["items"])
        assert all(r["union"] == 1 and r["shared"] == 1 for r in stats("overlap")["items"])
        offsets = stats("offsets", station_a=a.id, station_b=b.id)["items"]
        assert len(offsets) == 3 and all(r["title"] == "Music" for r in offsets)
        genres = stats("genres")
        assert genres["total"] == 6 and genres["coverage"] == 100
        summary = stats("summary")
        assert summary["total"] == 6 and summary["unique_tracks"] == 1
        assert summary["known_genres"] == 6 and summary["genre_coverage"] == 100
        assert sum(r["plays"] for r in stats("hourly", track_id=key)["items"]) == 0
        assert all(r["title"] == "Music" for r in stats("top"))
        assert all(
            r["title"] == "Music"
            for r in client.get("/api/plays", params=params, headers=auth).json()
        )
        assert all(
            r["title"] == "Music"
            for r in client.get("/api/explore", params=params, headers=auth).json()["items"]
        )
        assert all(
            r["title"] == "Music"
            for r in client.get(
                "/api/export", params={**params, "format": "json"}, headers=auth
            ).json()["items"]
        )
        history = client.get(f"/api/catalog/{key}/radio", params=params, headers=auth)
        if mode == "global" and not auth:
            assert history.status_code == 404
        else:
            assert history.json()["detections"] == 0
    assert db.query(Play).count() == 12
    if mode == "personal":
        assert client.get("/api/stats/genres").json()["total"] == 12


def test_bulk_promotion_is_idempotent_validated_and_protected(db, client):
    assert client.post("/api/admin/exclusions/bulk", json={"items": [NOISE]}).status_code == 401
    payload = {"items": [NOISE, NOISE, {"artist": None, "title": "Station slogan"}]}
    for _ in range(2):
        result = client.post("/api/admin/exclusions/bulk", json=payload, headers=AUTH)
        assert result.status_code == 200, result.text
        assert db.query(GlobalExclusion).count() == 2
    for items in ([], [NOISE] * 51, [NOISE, {"title": " "}], [NOISE, {"title": "x" * 501}]):
        assert (
            client.post(
                "/api/admin/exclusions/bulk", json={"items": items}, headers=AUTH
            ).status_code
            == 422
        )
        assert db.query(GlobalExclusion).count() == 2
    client.base_url = "https://testserver"
    login = client.post(
        "/api/admin/login",
        json={"token": "test-admin-token"},
        headers={
            "Origin": "https://testserver",
            "X-Admin-Login": "1",
        },
    )
    assert login.status_code == 200
    assert client.post("/api/admin/exclusions/bulk", json=payload).status_code == 403
    assert (
        client.post(
            "/api/admin/exclusions/bulk",
            json=payload,
            headers={
                "Origin": "https://testserver",
                "X-CSRF-Token": login.json()["csrf_token"],
            },
        ).status_code
        == 200
    )
