"""Small server-side admin sessions, login throttling and synchronizer CSRF tokens."""

import hashlib
import hmac
import secrets
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import monotonic
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import AdminSession

COOKIE = "__Host-radio_admin"
_attempts = OrderedDict()
_attempt_lock = Lock()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def signature(key):
    return hmac.new(
        settings.admin_token.get_secret_value().encode(), key.encode(), "sha256"
    ).hexdigest()


def verify_origin(request):
    origin = request.headers.get("origin", "")
    try:
        parsed = urlsplit(origin)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    except ValueError as error:
        raise HTTPException(403, "Origine de la requête refusée.") from error
    host = request.headers.get("host", "").lower()
    if (
        not origin
        or parsed.netloc.lower() != host
        or parsed.username
        or parsed.path
        or parsed.query
        or parsed.fragment
        or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
        or request.headers.get("sec-fetch-site") == "cross-site"
    ):
        raise HTTPException(403, "Origine de la requête refusée.")


def login_budget(request, *, consume=True):
    now = monotonic()
    peer = request.client.host if request.client else "unknown"
    with _attempt_lock:
        for key in list(_attempts):
            if _attempts[key][1] <= now:
                del _attempts[key]
        for key, limit in (("global", 100), (f"ip:{peer}", 5)):
            count, deadline = _attempts.get(key, (0, now + 900))
            if count >= limit or (key not in _attempts and len(_attempts) >= 1024):
                raise HTTPException(
                    429,
                    "Trop de tentatives. Réessayez dans 15 minutes.",
                    headers={"Retry-After": str(max(1, int(deadline - now)))},
                )
        if consume:
            for key in ("global", f"ip:{peer}"):
                count, deadline = _attempts.get(key, (0, now + 900))
                _attempts[key] = (count + 1, deadline)


def current_session(request: Request, db: Session):
    secret = settings.admin_token.get_secret_value()
    cookie = request.cookies.get(COOKIE, "")
    if not secret or not cookie or len(cookie) > 100:
        return None
    key = digest(cookie)
    entry = db.get(AdminSession, key)
    if entry is None:
        return None
    expiry = entry.expires_at.replace(tzinfo=UTC)
    if expiry <= datetime.now(UTC) or not secrets.compare_digest(
        entry.key_signature, signature(key)
    ):
        return None
    return entry


def session_state(entry):
    if entry is None:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "csrf_token": entry.csrf_token,
        "expires_at": entry.expires_at.replace(tzinfo=UTC),
    }


def establish_session(request, response, db):
    now = datetime.now(UTC)
    db.execute(delete(AdminSession).where(AdminSession.expires_at <= now))
    # Bound disk usage even if the correct key is used by many clients.
    old = list(
        db.scalars(select(AdminSession.id).order_by(AdminSession.expires_at.desc()).offset(19))
    )
    if old:
        db.execute(delete(AdminSession).where(AdminSession.id.in_(old)))
    previous = current_session(request, db)
    if previous:
        db.delete(previous)
    secret = secrets.token_urlsafe(32)
    key = digest(secret)
    entry = AdminSession(
        id=key,
        csrf_token=secrets.token_urlsafe(32),
        key_signature=signature(key),
        expires_at=now + timedelta(hours=settings.admin_session_hours),
    )
    db.add(entry)
    db.commit()
    response.set_cookie(COOKIE, secret, httponly=True, secure=True, samesite="strict", path="/")
    peer = request.client.host if request.client else "unknown"
    with _attempt_lock:
        _attempts.pop(f"ip:{peer}", None)
    return session_state(entry)


def require_admin(request: Request, db: Annotated[Session, Depends(get_db)]):
    expected = settings.admin_token.get_secret_value()
    if not expected:
        raise HTTPException(503, "Administration désactivée : configurer ADMIN_TOKEN côté serveur.")
    # Keep explicit server/CLI clients compatible; browsers use only the session cookie.
    supplied = request.headers.get("authorization", "")
    if supplied:
        # Share the login budget across routes without throttling normal API usage.
        login_budget(request, consume=False)
        if secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
            return
        login_budget(request)
        raise HTTPException(401, "Clé administrateur invalide.")
    entry = current_session(request, db)
    if entry is None:
        raise HTTPException(401, "Session administrateur expirée ou absente.")
    if (
        request.method not in {"GET", "HEAD", "OPTIONS"}
        or request.url.path == "/api/catalog/spotify-match"
    ):
        verify_origin(request)
        supplied_csrf = request.headers.get("x-csrf-token", "")
        if not secrets.compare_digest(supplied_csrf.encode(), entry.csrf_token.encode()):
            raise HTTPException(403, "Vérification CSRF refusée.")
