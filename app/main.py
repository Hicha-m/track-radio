from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Play, Station
from app.schemas import PlayOut, StationOut, StatsOut

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
            id=play.id, station_id=play.station_id, station_name=name, artist=play.artist,
            title=play.title, detected_at=play.detected_at, last_seen_at=play.last_seen_at,
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
        query.group_by(Play.artist, Play.title)
        .order_by(func.count(Play.id).desc())
        .limit(limit))
    return [StatsOut(artist=a, title=t, plays=c) for a, t, c in db.execute(query)]


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: DbSession):
    since = datetime.now(UTC) - timedelta(hours=24)
    stations_list = list(db.scalars(select(Station).order_by(Station.name)))
    recent_query = play_query(None, None, None).order_by(Play.detected_at.desc()).limit(25)
    recent = list(db.execute(recent_query))
    top = list(
        db.execute(
            select(Play.artist, Play.title, func.count(Play.id))
            .where(Play.detected_at >= since)
            .group_by(Play.artist, Play.title)
            .order_by(func.count(Play.id).desc())
            .limit(10)
        )
    )
    return templates.TemplateResponse(
        request, "dashboard.html", {"stations": stations_list, "recent": recent, "top": top}
    )
