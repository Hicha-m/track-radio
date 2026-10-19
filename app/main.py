import hashlib
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.catalog_api import router as catalog_router
from app.config import settings
from app.database import get_db
from app.models import Play, Station
from app.schemas import PlayOut, StationCreate, StationOut, StatsOut
from app.security import SecurityMiddleware, require_admin
from app.services.catalog import attach_metadata
from app.services.explorer import ExploreFilters, explore
from app.services.network import resolve_public, validate_url
from app.services.stats import cached_top

app = FastAPI(title="Radio Tracker", version="0.1.0")
app.add_middleware(SecurityMiddleware)
app.include_router(catalog_router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
ASSET_VERSION = hashlib.sha256(
    b"".join(p.read_bytes() for p in sorted(Path("app/static").glob("*")) if p.is_file())
).hexdigest()[:12]
DbSession = Annotated[Session, Depends(get_db)]


@app.get("/health")
def health(db: DbSession):
    db.execute(select(1))
    return {"status": "ok"}


@app.get("/api/version")
def version():
    return {"revision": os.getenv("APP_REVISION", "development"), "assets": ASSET_VERSION}


@app.get("/api/explore")
def explore_database(db: DbSession, filters: Annotated[ExploreFilters, Query()]):
    result = explore(db, filters)
    return {**result, "items": attach_metadata(db, result["items"])}


@app.get("/api/stations", response_model=list[StationOut])
def stations(db: DbSession):
    return list(db.scalars(select(Station).order_by(Station.name)))


def station_slug(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug or "radio"


@app.post(
    "/api/stations",
    response_model=StationOut,
    status_code=201,
    dependencies=[Depends(require_admin)],
)
def create_station(payload: StationCreate, db: DbSession):
    name = payload.name.strip()
    stream_url = payload.stream_url.strip()
    try:
        resolve_public(validate_url(stream_url))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if db.get_bind().dialect.name == "sqlite" and not db.in_transaction():
        # Serialize quota and uniqueness checks across concurrent additions.
        db.execute(text("BEGIN IMMEDIATE"))
    if db.scalar(select(func.count(Station.id))) >= settings.max_stations:
        raise HTTPException(409, "Nombre maximal de radios atteint.")
    if db.scalar(select(Station).where(Station.name == name)):
        raise HTTPException(status_code=409, detail="Cette radio existe déjà.")

    base_slug = station_slug(name)
    slug = base_slug[:90]
    suffix = 2
    while db.scalar(select(Station).where(Station.slug == slug)):
        slug = f"{base_slug[:90]}-{suffix}"
        suffix += 1

    station = Station(
        name=name,
        slug=slug,
        stream_url=stream_url,
        enabled=payload.enabled,
        source_type="icy",
    )
    db.add(station)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, "Une radio portant ce nom existe déjà.") from error
    db.refresh(station)
    return station


def play_query(station_id: int | None, since: datetime | None, until: datetime | None):
    query = select(Play, Station.name).join(Station)
    if station_id:
        query = query.where(Play.station_id == station_id)
    if since:
        query = query.where(Play.detected_at >= since)
    if until:
        query = query.where(Play.detected_at < until)
    return query


@app.get("/api/plays", response_model=list[PlayOut])
def plays(
    db: DbSession,
    station_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
):
    query = play_query(station_id, since, until).order_by(Play.detected_at.desc()).limit(limit)
    rows = db.execute(query)
    return [
        PlayOut(
            id=play.id,
            station_id=play.station_id,
            station_name=name,
            artist=play.artist,
            title=play.title,
            detected_at=play.detected_at,
            last_seen_at=play.last_seen_at,
            source=play.source,
        )
        for play, name in rows
    ]


@app.get("/api/stats/top", response_model=list[StatsOut])
def top_tracks(
    db: DbSession,
    station_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    enriched = attach_metadata(
        db,
        [
            {"artist": a, "title": t, "plays": c}
            for a, t, c in cached_top(db, station_id, since, until, limit)
        ],
    )
    return [StatsOut(**track) for track in enriched]


def parse_station_id(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="station_id doit être un entier.") from error


@app.get("/", response_class=HTMLResponse)
@app.get("/recherche", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: DbSession,
    station_id: str | None = None,
):
    station_id = parse_station_id(station_id)
    stations_list = list(db.scalars(select(Station).order_by(Station.name)))
    selected_station = db.get(Station, station_id) if station_id else None
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "stations": stations_list,
            "station_id": station_id,
            "selected_station": selected_station,
            "is_search": request.url.path == "/recherche",
            "asset_version": ASSET_VERSION,
        },
    )
