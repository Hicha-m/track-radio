"""Bounded exports of all matching rows, independent of the displayed page."""

import csv
import io
import json
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.catalog import attach_metadata
from app.services.explorer import ExploreFilters, result_query

router = APIRouter()
MAX_EXPORT_ROWS = 10000


class ExportFilters(ExploreFilters):
    format: Literal["csv", "json"] = "csv"


def spotify_search(artist, title):
    return "https://open.spotify.com/search/" + quote(f"{artist or ''} {title}".strip(), safe="")


def serializable(value):
    if isinstance(value, datetime):
        return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).isoformat()
    return value


def export_row(row):
    data = {k: serializable(v) for k, v in row.items() if k != "metadata"}
    metadata = row.get("metadata") or {}
    for key in (
        "track_id",
        "recording_id",
        "provider",
        "provider_id",
        "duration",
        "cover_url",
        "primary_genre",
        "genre_source",
    ):
        data[key] = metadata.get(key)
    data["spotify_url"] = (
        metadata.get("url")
        if metadata.get("provider") == "spotify"
        else spotify_search(row.get("artist"), row["title"])
    )
    return data


def safe_cell(value):
    # Radio metadata is untrusted text; never export executable spreadsheet formulas.
    if isinstance(value, str) and (
        value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))
    ):
        return "'" + value
    return value


@router.get("/api/export")
def export(db: Annotated[Session, Depends(get_db)], filters: Annotated[ExportFilters, Query()]):
    selection = ExploreFilters(**filters.model_dump(exclude={"format"}))
    query, snapshot = result_query(db, selection)
    count = db.scalar(
        select(func.count()).select_from(query.order_by(None).limit(MAX_EXPORT_ROWS + 1).subquery())
    )
    if count > MAX_EXPORT_ROWS:
        raise HTTPException(
            422, "L’export dépasse 10 000 lignes. Précisez les dates ou les filtres."
        )
    columns = (
        ["artist", "title", "plays", "stations", "first_at", "last_at"]
        if filters.view == "tracks"
        else [
            "id",
            "station_id",
            "station_name",
            "artist",
            "title",
            "detected_at",
            "last_seen_at",
            "source",
            "raw_metadata",
        ]
    )
    columns += [
        "track_id",
        "recording_id",
        "provider",
        "provider_id",
        "duration",
        "cover_url",
        "spotify_url",
        "primary_genre",
        "genre_source",
    ]

    def rows():
        result = db.execute(query.limit(MAX_EXPORT_ROWS).execution_options(yield_per=50)).mappings()
        try:
            for batch in result.partitions(50):
                yield from (export_row(r) for r in attach_metadata(db, [dict(r) for r in batch]))
        finally:
            result.close()

    def content():
        if filters.format == "json":
            yield (
                json.dumps({"view": filters.view, "snapshot_id": snapshot, "count": count})[:-1]
                + ',"items":['
            )
            first = True
            for row in rows():
                yield ("" if first else ",") + json.dumps(row, ensure_ascii=False)
                first = False
            yield "]}"
        else:
            yield "\ufeff"
            buffer = io.StringIO()
            writer = csv.DictWriter(
                buffer, fieldnames=columns, delimiter=";", extrasaction="ignore"
            )
            writer.writeheader()
            yield buffer.getvalue()
            for row in rows():
                buffer.seek(0)
                buffer.truncate(0)
                writer.writerow({k: safe_cell(v) for k, v in row.items()})
                yield buffer.getvalue()

    return StreamingResponse(
        content(),
        media_type="application/json" if filters.format == "json" else "text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="radio-{filters.view}.{filters.format}"',
            "Cache-Control": "no-store",
        },
    )
