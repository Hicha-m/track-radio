import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin_api import router as admin_router
from app.admin_api import session_router
from app.artwork_api import router as artwork_router
from app.catalog_api import router as catalog_router
from app.database import get_db
from app.export_api import router as export_router
from app.metrics_api import router as metrics_router
from app.models import Play, Station, TrackMetadata
from app.schemas import PlayOut, StationOut, StatsOut
from app.security import SecurityMiddleware
from app.services.catalog import attach_metadata
from app.services.exclusions import canonical_exclusions, exclusion_conditions, global_revision
from app.services.explorer import ExploreFilters, explore
from app.services.stats import cached_top

app = FastAPI(title="Radio Tracker", version="0.1.0")
app.add_middleware(SecurityMiddleware)
app.include_router(catalog_router)
app.include_router(session_router)
app.include_router(admin_router)
app.include_router(export_router)
app.include_router(artwork_router)
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


@app.get("/api/exclusions/revision")
def exclusions_revision(db: DbSession):
    from fastapi.responses import JSONResponse

    return JSONResponse({"revision": global_revision(db)}, headers={"Cache-Control": "no-store"})


@app.get("/api/explore")
def explore_database(db: DbSession, filters: Annotated[ExploreFilters, Query()]):
    result = explore(db, filters)
    return {**result, "items": attach_metadata(db, result["items"])}


@app.get("/api/stations", response_model=list[StationOut])
def stations(db: DbSession):
    return list(db.scalars(select(Station).order_by(Station.name)))


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
    query = (
        play_query(station_id, since, until)
        .where(*exclusion_conditions("", db.get_bind().dialect.name == "sqlite"))
        .order_by(Play.detected_at.desc())
        .limit(limit)
    )
    rows = db.execute(query)
    enriched = attach_metadata(
        db,
        [
            PlayOut(
                id=play.id,
                station_id=play.station_id,
                station_name=name,
                artist=play.artist,
                title=play.title,
                detected_at=play.detected_at,
                last_seen_at=play.last_seen_at,
                source=play.source,
            ).model_dump()
            for play, name in rows
        ],
    )
    return [PlayOut(**row) for row in enriched]


@app.get("/api/stats/top", response_model=list[StatsOut])
def top_tracks(
    db: DbSession,
    station_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    exclusions: Annotated[str, Query(max_length=12000)] = "",
):
    try:
        exclusions = canonical_exclusions(exclusions)
    except ValueError as error:
        raise HTTPException(422, "Liste de titres masqués invalide.") from error
    enriched = attach_metadata(
        db,
        [
            {"artist": a, "title": t, "plays": c}
            for a, t, c in cached_top(db, station_id, since, until, limit, exclusions)
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
@app.get("/stats", response_class=HTMLResponse)
@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin/catalogue", response_class=HTMLResponse)
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
            "page": request.url.path,
            "genres": list(
                db.scalars(
                    select(TrackMetadata.primary_genre)
                    .where(TrackMetadata.primary_genre.is_not(None))
                    .distinct()
                    .order_by(TrackMetadata.primary_genre)
                )
            ),
            "asset_version": ASSET_VERSION,
        },
    )


app.include_router(metrics_router)
