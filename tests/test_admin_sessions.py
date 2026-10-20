from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.admin_sessions import COOKIE, _attempts
from app.config import settings
from app.models import AdminSession

ORIGIN = {"Origin": "https://testserver", "X-Admin-Login": "1"}


@pytest.fixture
def browser_client(client):
    client.base_url = "https://testserver"
    client.cookies.clear()
    _attempts.clear()
    yield client
    _attempts.clear()


def login(client):
    result = client.post("/api/admin/login", json={"token": "test-admin-token"}, headers=ORIGIN)
    assert result.status_code == 200, result.text
    return result


def test_cookie_session_navigation_csrf_logout_and_replay(browser_client, db):
    client = browser_client
    assert client.get("/api/admin/session").json() == {"authenticated": False}
    result = login(client)
    header = result.headers["set-cookie"]
    assert "HttpOnly" in header and "Secure" in header and "SameSite=strict" in header
    assert "Path=/" in header and "Domain=" not in header
    assert result.headers["cache-control"] == "no-store"
    csrf = result.json()["csrf_token"]
    cookie = client.cookies.get(COOKIE)
    stored = db.scalar(select(AdminSession))
    assert cookie != stored.id and "test-admin-token" not in repr(stored.__dict__)
    for path in ["/", "/recherche", "/admin/catalogue", "/api/admin/catalog"]:
        assert client.get(path).status_code == 200
    assert client.get("/api/admin/session").json()["authenticated"]
    assert client.post("/api/admin/logout", json={}).status_code == 403
    assert (
        client.post(
            "/api/admin/logout",
            json={},
            headers={"Origin": "https://evil.test", "X-CSRF-Token": csrf},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/admin/logout",
            json={},
            headers={"Origin": "https://testserver", "X-CSRF-Token": "wrong"},
        ).status_code
        == 403
    )
    result = client.post("/api/admin/logout", json={}, headers={**ORIGIN, "X-CSRF-Token": csrf})
    assert result.status_code == 200
    assert not client.get("/api/admin/session").json()["authenticated"]
    assert db.scalar(select(AdminSession)) is None
    assert (
        client.get("/api/admin/catalog", headers={"Cookie": f"{COOKIE}={cookie}"}).status_code
        == 401
    )


def test_expiration_and_rotation_invalidate_session(browser_client, db, monkeypatch):
    client = browser_client
    login(client)
    stored = db.scalar(select(AdminSession))
    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert client.get("/api/admin/catalog").status_code == 401
    assert not client.get("/api/admin/session").json()["authenticated"]
    login(client)
    monkeypatch.setattr(settings, "admin_token", SecretStr("rotated-key"))
    assert client.get("/api/admin/catalog").status_code == 401
    assert not client.get("/api/admin/session").json()["authenticated"]


def test_login_origin_custom_header_throttling_and_disabled_admin(browser_client, monkeypatch):
    client = browser_client
    assert client.post("/api/admin/login", json={"token": "test-admin-token"}).status_code == 403
    assert (
        client.post(
            "/api/admin/login",
            json={"token": "test-admin-token"},
            headers={**ORIGIN, "Origin": "https://evil.test"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/admin/login",
            json={"token": "test-admin-token"},
            headers={**ORIGIN, "Origin": "https://[invalid"},
        ).status_code
        == 403
    )
    for _ in range(5):
        assert (
            client.post("/api/admin/login", json={"token": "wrong"}, headers=ORIGIN).status_code
            == 401
        )
    limited = client.post("/api/admin/login", json={"token": "test-admin-token"}, headers=ORIGIN)
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    _attempts.clear()
    monkeypatch.setattr(settings, "admin_token", SecretStr(""))
    assert (
        client.post(
            "/api/admin/login", json={"token": "test-admin-token"}, headers=ORIGIN
        ).status_code
        == 503
    )
    assert not client.get("/api/admin/session").json()["authenticated"]


def test_login_rotates_cookie_and_keeps_session_count_bounded(browser_client, db):
    client = browser_client
    first = login(client)
    cookie = client.cookies.get(COOKIE)
    second = login(client)
    assert first.json()["csrf_token"] != second.json()["csrf_token"]
    assert client.cookies.get(COOKIE) != cookie
    assert len(list(db.scalars(select(AdminSession)))) == 1
    assert (
        client.get("/api/admin/catalog", headers={"Cookie": f"{COOKIE}={cookie}"}).status_code
        == 401
    )


def test_bearer_attempts_share_login_budget_across_routes_and_expire(browser_client, monkeypatch):
    from app import admin_sessions

    client = browser_client
    now = 1000.0
    monkeypatch.setattr(admin_sessions, "monotonic", lambda: now)
    valid = {"Authorization": "Bearer test-admin-token"}
    invalid = {"Authorization": "Bearer wrong"}
    # Authenticated API requests do not consume the guessing budget.
    for _ in range(10):
        assert client.get("/api/admin/access", headers=valid).status_code == 200
    assert client.get("/api/admin/access", headers=invalid).status_code == 401
    for _ in range(4):
        assert (
            client.post("/api/admin/login", json={"token": "wrong"}, headers=ORIGIN).status_code
            == 401
        )
    for path in ("/api/admin/access", "/api/admin/stations", "/api/catalog/spotify-match"):
        response = client.get(path, headers=valid)
        assert response.status_code == 429
        assert int(response.headers["retry-after"]) == 900
    now += 901
    assert client.get("/api/admin/access", headers=valid).status_code == 200
    assert client.get("/api/admin/access", headers=invalid).status_code == 401


def test_bearer_global_budget_covers_different_ips(browser_client):
    from fastapi import HTTPException
    from starlette.requests import Request

    from app.admin_sessions import login_budget

    for index in range(100):
        request = Request({"type": "http", "client": (f"192.0.2.{index}", 1234)})
        login_budget(request)
    response = browser_client.get("/api/admin/access", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 429
    with pytest.raises(HTTPException) as blocked:
        login_budget(Request({"type": "http", "client": ("192.0.2.200", 1234)}))
    assert blocked.value.status_code == 429


def test_cookie_correction_requires_csrf_and_invalidates_read_caches(browser_client, db):
    from app.services.catalog import ensure_entry

    entry = ensure_entry(db, "Artist", "Song")
    entry.candidates = [
        {
            "provider": "itunes",
            "provider_id": "42",
            "title": "Official Song",
            "artist": "Artist",
            "genres": ["Rap"],
            "genre_source": "itunes",
        }
    ]
    db.commit()
    client = browser_client
    response = login(client)
    assert "test-admin-token" not in response.text
    path = f"/api/catalog/{entry.id}/correction"
    payload = {"candidate_id": "itunes:42"}
    assert client.post(path, json=payload).status_code == 403
    assert (
        client.post(
            path, json=payload, headers={**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
        ).status_code
        == 200
    )
    result = client.get("/api/catalog?artist=Artist&title=Song").json()
    assert result["manual"] and result["title"] == "Official Song"
    assert result["primary_genre"] == "Rap"
    assert "candidates" not in result


def test_session_table_stays_bounded_and_expired_rows_are_removed(browser_client, db):
    for index in range(30):
        db.add(
            AdminSession(
                id=f"{index:064x}",
                csrf_token="x" * 43,
                key_signature="x" * 64,
                expires_at=datetime.now(UTC) + timedelta(hours=index - 2),
            )
        )
    db.commit()
    login(browser_client)
    rows = list(db.scalars(select(AdminSession)))
    assert len(rows) <= 20
    assert all(row.expires_at.replace(tzinfo=UTC) > datetime.now(UTC) for row in rows)
