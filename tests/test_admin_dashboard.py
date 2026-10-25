import json
from datetime import UTC, datetime, timedelta

from app.admin_sessions import _attempts
from app.models import Play, Station
from app.services.catalog import ensure_entry

AUTH = {"Authorization": "Bearer test-admin-token"}
PAGES = ("dashboard", "radios", "diffusions", "catalogue", "exclusions")


def test_dashboard_excludes_personal_and_global_tracks_before_aggregation(db, client):
    station = Station(name="Test", slug="test", stream_url="https://example.test/live")
    db.add(station)
    db.flush()
    now = datetime.now(UTC)
    for i, title in enumerate(("Visible", "Personnel", "Global", "Ancien")):
        at = now - timedelta(hours=30 if title == "Ancien" else 3 - i)
        entry = ensure_entry(db, "Artiste", title)
        entry.status = "error"
        db.add(
            Play(
                station_id=station.id,
                artist="Artiste",
                title=title,
                raw_metadata=title,
                source="icy",
                detected_at=at,
                last_seen_at=at,
            )
        )
    db.commit()
    assert client.get("/api/admin/dashboard").status_code == 401
    assert (
        client.post(
            "/api/admin/exclusions", json={"artist": "Artiste", "title": "Global"}, headers=AUTH
        ).status_code
        == 200
    )
    params = {"exclusions": json.dumps([{"artist": "ARTISTE", "title": "Personnel"}])}
    response = client.get("/api/admin/dashboard", params=params, headers=AUTH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert {
        k: data[k] for k in ("active_stations", "visible_tracks", "review_tracks", "recent_plays")
    } == {
        "active_stations": 1,
        "visible_tracks": 2,
        "review_tracks": 2,
        "recent_plays": 1,
    }
    assert datetime.fromisoformat(data["last_detection"]) == now - timedelta(hours=3)
    assert db.query(Play).count() == 4
    assert client.get("/api/admin/dashboard", headers=AUTH).json()["recent_plays"] == 2
    assert (
        client.get(
            "/api/admin/dashboard", params={"exclusions": "invalid"}, headers=AUTH
        ).status_code
        == 422
    )


def test_empty_dashboard_has_no_recent_detection(client):
    data = client.get("/api/admin/dashboard", headers=AUTH).json()
    assert data == {
        "active_stations": 0,
        "visible_tracks": 0,
        "review_tracks": 0,
        "recent_plays": 0,
        "last_detection": None,
    }


def test_admin_routes_require_session_and_render_only_their_tools(client):
    client.base_url = "https://testserver"
    _attempts.clear()
    for page in PAGES:
        response = client.get(f"/admin/{page}", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == f"/admin?next=/admin/{page}"
        assert response.headers["cache-control"] == "no-store"
    login_page = client.get("/admin").text
    response = client.get("/admin/catalogue?review=true", follow_redirects=False)
    assert response.headers["location"] == "/admin?next=/admin/catalogue%3Freview%3Dtrue"
    assert 'id="admin-login"' in login_page
    assert 'id="admin-workspace"' not in login_page
    response = client.post(
        "/api/admin/login",
        json={"token": "test-admin-token"},
        headers={"Origin": "https://testserver", "X-Admin-Login": "1"},
    )
    assert response.status_code == 200
    assert client.get("/admin", follow_redirects=False).headers["location"] == "/admin/dashboard"
    sections = {
        "radios": "admin-stations",
        "diffusions": "admin-plays-form",
        "catalogue": "admin-catalog-form",
        "exclusions": "admin-exclusion-search",
    }
    for page in PAGES:
        response = client.get(f"/admin/{page}")
        assert response.status_code == 200
        assert 'id="admin-login"' not in response.text
        for name, section in sections.items():
            assert (f'id="{section}"' in response.text) == (page == name)
    _attempts.clear()
