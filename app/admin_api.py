"""Administrative reads and station collection controls."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.orm import Session, load_only

from app.admin_sessions import (
    COOKIE,
    current_session,
    establish_session,
    login_budget,
    session_state,
    verify_origin,
)
from app.config import settings
from app.database import get_db
from app.models import GlobalExclusion, Play, Station, TrackMetadata
from app.security import require_admin
from app.services.catalog import public_metadata
from app.services.explorer import text_match

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])
Db = Annotated[Session, Depends(get_db)]
session_router = APIRouter(prefix="/api/admin")


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = Field(min_length=1, max_length=512)


@session_router.get("/session")
def session(request: Request, db: Db):
    return session_state(current_session(request, db))


@session_router.post("/login")
def login(request: Request, response: Response, payload: Login, db: Db):
    verify_origin(request)
    if request.headers.get("x-admin-login") != "1":
        raise HTTPException(403, "En-tête de connexion requis.")
    expected = settings.admin_token.get_secret_value()
    if not expected:
        raise HTTPException(503, "Administration désactivée côté serveur.")
    login_budget(request)
    if not secrets.compare_digest(payload.token.get_secret_value().encode(), expected.encode()):
        raise HTTPException(401, "Clé administrateur invalide.")
    return establish_session(request, response, db)


@session_router.post("/logout", dependencies=[Depends(require_admin)])
def logout(request: Request, response: Response, db: Db):
    entry = current_session(request, db)
    if entry:
        db.delete(entry)
        db.commit()
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return {"authenticated": False}


class StationPreference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool | None


def station_data(station):
    return {
        "id": station.id,
        "name": station.name,
        "enabled": station.enabled,
        "enabled_override": station.enabled_override,
        "configured": station.configured,
        "config_enabled": station.config_enabled,
    }


@router.get("/access")
def access():
    return {"ok": True}


@router.get("/stations")
def stations(db: Db):
    return [station_data(s) for s in db.scalars(select(Station).order_by(Station.name))]


@router.patch("/stations/{station_id}")
def station_preference(station_id: int, payload: StationPreference, db: Db):
    station = db.get(Station, station_id)
    if station is None:
        raise HTTPException(404, "Radio introuvable.")
    if not station.configured and payload.enabled is True:
        raise HTTPException(409, "Cette radio a été retirée de la configuration.")
    station.enabled_override = payload.enabled
    station.enabled = station.configured and (
        payload.enabled if payload.enabled is not None else station.config_enabled
    )
    db.commit()
    return station_data(station)


def admin_metadata(entry, details=True):
    result = {
        **public_metadata(entry),
        "artist": entry.artist,
        "title": entry.title,
        "automatic_done": entry.automatic_done,
    }
    if details:
        result.update(
            candidates=entry.candidates,
            lyrics_candidates=entry.lyrics_candidates,
            lyrics=entry.lyrics,
            attempts=entry.attempts,
        )
    return result


@router.get("/catalog")
def catalogue(
    db: Db,
    q: str = Query("", max_length=200),
    review: bool = False,
    page: int = Query(1, ge=1, le=1000000),
):
    recent = (
        select(Play.track_id, func.count().label("plays"))
        .where(Play.detected_at >= datetime.now(UTC) - timedelta(days=7))
        .group_by(Play.track_id)
        .subquery()
    )
    doubtful = case((TrackMetadata.status.in_(["ambiguous", "error"]), 1), else_=0)
    count = func.coalesce(recent.c.plays, 0)
    query = (
        select(TrackMetadata, count)
        .options(
            load_only(
                TrackMetadata.id,
                TrackMetadata.artist,
                TrackMetadata.title,
                TrackMetadata.selected,
                TrackMetadata.status,
                TrackMetadata.manual,
                TrackMetadata.lyrics_status,
                TrackMetadata.primary_genre,
                TrackMetadata.automatic_done,
            )
        )
        .outerjoin(recent, TrackMetadata.id == recent.c.track_id)
    )
    unicode = db.get_bind().dialect.name == "sqlite"
    for word in q.split():
        query = query.where(
            or_(
                text_match(TrackMetadata.artist, word, unicode=unicode),
                text_match(TrackMetadata.title, word, unicode=unicode),
            )
        )
    if review:
        query = query.where(
            or_(
                TrackMetadata.status.in_(["ambiguous", "not_found", "error"]),
                TrackMetadata.lyrics_status.in_(["ambiguous", "not_found", "error"]),
            )
        )
    rows = db.execute(
        query.order_by(doubtful.desc(), count.desc(), TrackMetadata.title, TrackMetadata.id)
        .offset((page - 1) * 25)
        .limit(26)
    ).all()
    return {
        "items": [{**admin_metadata(e, details=False), "recent_plays": n} for e, n in rows[:25]],
        "page": page,
        "has_more": len(rows) > 25,
    }


@router.get("/catalog/{key}")
def catalogue_entry(key: str, db: Db):
    entry = db.get(TrackMetadata, key)
    if entry is None:
        raise HTTPException(404, "Fiche introuvable.")
    return admin_metadata(entry)


class GlobalHiddenTrack(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    artist: str | None = Field(None, max_length=300)
    title: str = Field(min_length=1, max_length=500)


@router.get("/exclusions")
def global_exclusions(db: Db):
    from app.services.exclusions import global_revision

    entries = db.scalars(
        select(GlobalExclusion).order_by(GlobalExclusion.title, GlobalExclusion.id)
    )
    return {
        "items": [{"id": t.id, "artist": t.artist, "title": t.title} for t in entries],
        "revision": global_revision(db),
    }


@router.post("/exclusions")
def hide_globally(payload: GlobalHiddenTrack, db: Db):
    from sqlalchemy.exc import IntegrityError

    from app.services.exclusions import exclusion_id, global_revision, normalized_pair

    artist = payload.artist or None
    key = exclusion_id(artist, payload.title)
    if db.get(GlobalExclusion, key) is None:
        a, t = normalized_pair(artist, payload.title)
        db.add(
            GlobalExclusion(
                id=key,
                artist=artist,
                title=payload.title,
                artist_normalized=a,
                title_normalized=t,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if db.get(GlobalExclusion, key) is None:
                raise
    return {"id": key, "revision": global_revision(db)}


@router.delete("/exclusions/{key}")
def restore_globally(key: str, db: Db):
    from app.services.exclusions import global_revision

    db.execute(delete(GlobalExclusion).where(GlobalExclusion.id == key))
    db.commit()
    return {"revision": global_revision(db)}
