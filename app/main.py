import re
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Play, Station
from app.schemas import PlayOut, StationCreate, StationOut, StatsOut

app = FastAPI(title="Radio Tracker", version="0.1.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
DbSession = Annotated[Session, Depends(get_db)]


@app.get("/health")
def health(db: DbSession):
    db.execute(select(1))
    return {"status": "ok"}


@app.get("/api/stations", response_model=list[StationOut])
def stations(db: DbSession):
    return list(db.scalars(select(Station).order_by(Station.name)))


def make_slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.casefold()).strip("-")


@app.post("/api/stations", response_model=StationOut, status_code=status.HTTP_201_CREATED)
def create_station(payload: StationCreate, db: DbSession):
    if db.scalar(select(Station).where(Station.stream_url == payload.stream_url)):
        raise HTTPException(status_code=409, detail="Ce flux existe déjà")
    if db.scalar(select(Station).where(Station.name == payload.name.strip())):
        raise HTTPException(status_code=409, detail="Ce nom de radio existe déjà")
    base_slug = make_slug(payload.name) or "radio"
    slug = base_slug
    suffix = 2
    while db.scalar(select(Station).where(Station.slug == slug)):
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    station = Station(
        name=payload.name.strip(),
        slug=slug,
        stream_url=payload.stream_url,
        source_type="icy",
        enabled=payload.enabled,
    )
    db.add(station)
    db.commit()
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
    query = select(Play.artist, Play.title, func.count(Play.id).label("plays"))
    if station_id:
        query = query.where(Play.station_id == station_id)
    if since:
        query = query.where(Play.detected_at >= since)
    if until:
        query = query.where(Play.detected_at < until)
    query = (
        query.group_by(Play.artist, Play.title).order_by(func.count(Play.id).desc()).limit(limit)
    )
    return [StatsOut(artist=a, title=t, plays=c) for a, t, c in db.execute(query)]


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: DbSession,
    station_id: int | None = None,
    hours: int = Query(24, ge=0, le=24 * 365),
):
    since = datetime.now(UTC) - timedelta(hours=hours) if hours else None
    stations_list = list(db.scalars(select(Station).order_by(Station.name)))
    selected_station = db.get(Station, station_id) if station_id else None
    recent_query = play_query(station_id, since, None).order_by(Play.detected_at.desc()).limit(100)
    recent = list(db.execute(recent_query))
    top_query = select(Play.artist, Play.title, func.count(Play.id))
    if station_id:
        top_query = top_query.where(Play.station_id == station_id)
    if since:
        top_query = top_query.where(Play.detected_at >= since)
    top = list(
        db.execute(
            top_query.group_by(Play.artist, Play.title)
            .order_by(func.count(Play.id).desc())
            .limit(20)
        )
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "stations": stations_list,
            "recent": recent,
            "top": top,
            "station_id": station_id,
            "hours": hours,
            "selected_station": selected_station,
        },
    )
