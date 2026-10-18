"""Exploration paginée : aucune copie complète de la base dans le navigateur."""

from collections import OrderedDict
from datetime import UTC, datetime
from threading import Lock
from time import monotonic
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import String, cast, func, or_, select

from app.models import Play, Station
from app.services.search_text import normalize_search


class ExploreFilters(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    view: Literal["plays", "tracks"] = "plays"
    station_id: int | None = Field(None, ge=1, le=2**63 - 1)
    q: str = Field("", max_length=200)
    artist: str = Field("", max_length=300)
    title: str = Field("", max_length=500)
    raw: str = Field("", max_length=200)
    exact: bool = False
    source: Literal["icy", "json", "html"] | None = None
    artist_status: Literal["any", "known", "missing"] = "any"
    enabled: bool | None = None
    since: datetime | None = None
    until: datetime | None = None
    seen_since: datetime | None = None
    seen_until: datetime | None = None
    min_plays: int = Field(1, ge=1, le=10**9)
    sort: Literal["newest", "oldest", "artist", "title", "station", "frequency"] = "newest"
    page: int = Field(1, ge=1, le=1_000_000)
    page_size: int = Field(50, ge=1, le=100)
    snapshot_id: int | None = Field(None, ge=0, le=2**63 - 1)

    @model_validator(mode="after")
    def valid_dates(self):
        for field in ("since", "until", "seen_since", "seen_until"):
            value = getattr(self, field)
            if value is not None:
                setattr(
                    self,
                    field,
                    value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC),
                )
        for start, end in ((self.since, self.until), (self.seen_since, self.seen_until)):
            if start and end:
                start = start.replace(tzinfo=UTC) if start.tzinfo is None else start
                end = end.replace(tzinfo=UTC) if end.tzinfo is None else end
                if start >= end:
                    raise ValueError("La date de début doit précéder la date de fin.")
        return self


_cache = OrderedDict()
_lock = Lock()


def text_match(column, value, exact=False, unicode=True):
    # Treat %, _ and backslash as literal characters, never SQL search operators.
    if unicode:
        normalized = func.fold_text(func.coalesce(column, ""))
        value = normalize_search(value)
        return normalized == value if exact else normalized.contains(value, autoescape=True)
    if exact:
        return func.lower(func.coalesce(column, "")) == value.lower()
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


def conditions(filters, unicode=True):
    values = []
    for column, value, compare in (
        (Play.station_id, filters.station_id, "eq"),
        (Play.source, filters.source, "eq"),
        (Station.enabled, filters.enabled, "eq"),
        (Play.detected_at, filters.since, "ge"),
        (Play.detected_at, filters.until, "lt"),
        (Play.last_seen_at, filters.seen_since, "ge"),
        (Play.last_seen_at, filters.seen_until, "lt"),
        (Play.id, filters.snapshot_id, "le"),
    ):
        if value is not None:
            values.append(
                {
                    "eq": column.__eq__,
                    "ge": column.__ge__,
                    "lt": column.__lt__,
                    "le": column.__le__,
                }[compare](value)
            )
    if filters.q:
        values.append(
            or_(
                *(
                    text_match(column, filters.q, unicode=unicode)
                    for column in (Play.title, Play.artist, Station.name)
                )
            )
        )
    for column, value in ((Play.artist, filters.artist), (Play.title, filters.title)):
        if value:
            values.append(text_match(column, value, filters.exact, unicode))
    if filters.raw:
        values.append(text_match(Play.raw_metadata, filters.raw, unicode=unicode))
    missing = or_(Play.artist.is_(None), Play.artist == "")
    if filters.artist_status == "missing":
        values.append(missing)
    elif filters.artist_status == "known":
        values.append(~missing)
    return values


def explore(db, filters):
    key = (db.get_bind(), filters.model_dump_json())
    with _lock:
        cached = _cache.get(key)
        if cached and cached[0] > monotonic():
            _cache.move_to_end(key)
            return cached[1]
    result = query_results(db, filters)
    with _lock:
        _cache[key] = (monotonic() + 3, result)
        _cache.move_to_end(key)
        while len(_cache) > 32:
            _cache.popitem(last=False)
    return result


def query_results(db, filters):
    snapshot = filters.snapshot_id
    if snapshot is None:
        snapshot = db.scalar(select(func.max(Play.id))) or 0
    filters = filters.model_copy(update={"snapshot_id": snapshot})
    where = conditions(filters, unicode=db.get_bind().dialect.name == "sqlite")
    if filters.view == "tracks":
        count = func.count(Play.id)
        latest = func.max(Play.detected_at)
        query = (
            select(
                Play.artist,
                Play.title,
                count.label("plays"),
                func.count(func.distinct(Play.station_id)).label("stations"),
                func.min(Play.detected_at).label("first_at"),
                latest.label("last_at"),
            )
            .join(Station)
            .where(*where)
            .group_by(Play.artist, Play.title)
        )
        query = query.having(count >= filters.min_plays)
        order = {
            "oldest": (func.min(Play.detected_at).asc(), Play.title, Play.artist),
            "artist": (func.lower(Play.artist), Play.title),
            "title": (func.lower(Play.title), Play.artist),
            "frequency": (count.desc(), Play.title, Play.artist),
        }.get(filters.sort, (latest.desc(), Play.title, Play.artist))
    else:
        query = (
            select(
                Play.id,
                Play.station_id,
                Station.name.label("station_name"),
                Play.artist,
                Play.title,
                Play.detected_at,
                Play.last_seen_at,
                Play.source,
                Play.raw_metadata,
            )
            .join(Station)
            .where(*where)
        )
        date_order = Play.detected_at
        scan_filter = (
            any((filters.q, filters.artist, filters.title, filters.raw))
            or filters.source is not None
            or filters.enabled is not None
            or filters.artist_status != "any"
            or filters.seen_since is not None
            or filters.seen_until is not None
        )
        if db.get_bind().dialect.name == "sqlite" and scan_filter:
            # A date-index walk plus random table lookups is disastrous for a
            # selective non-indexed filter. Sort matching rows after scanning
            # them instead; SQLite timestamps are stored as sortable ISO text.
            date_order = cast(Play.detected_at, String)
        order = {
            "oldest": (date_order.asc(), Play.id.asc()),
            "artist": (func.lower(Play.artist), date_order.desc(), Play.id.desc()),
            "title": (func.lower(Play.title), date_order.desc(), Play.id.desc()),
            "station": (Station.name, date_order.desc(), Play.id.desc()),
        }.get(filters.sort, (date_order.desc(), Play.id.desc()))
    rows = list(
        db.execute(
            query.order_by(*order)
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size + 1)
        ).mappings()
    )
    items = [
        {
            key: value.replace(tzinfo=UTC)
            if isinstance(value, datetime) and value.tzinfo is None
            else value
            for key, value in row.items()
        }
        for row in rows[: filters.page_size]
    ]
    return {
        "items": items,
        "page": filters.page,
        "page_size": filters.page_size,
        "has_more": len(rows) > filters.page_size,
        "snapshot_id": snapshot,
    }
