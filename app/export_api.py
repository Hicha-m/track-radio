"""Bounded exports of all matching rows, independent of the displayed page."""

import csv
import io
import json
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import TrackMetadata
from app.services.catalog import attach_metadata
from app.services.explorer import ExploreFilters, result_query

router = APIRouter()
MAX_EXPORT_ROWS = 10000

# Ordered public schema shared by validation, CSV headers and the export form.
TRACK_FIELDS = {
    "artist": "Artiste",
    "title": "Titre",
    "plays": "Nombre de diffusions",
    "stations": "Nombre de radios",
    "first_at": "Première diffusion",
    "last_at": "Dernière diffusion",
}
PLAY_FIELDS = {
    "id": "Identifiant du passage",
    "station_id": "Identifiant de la radio",
    "station_name": "Radio",
    "artist": "Artiste",
    "title": "Titre",
    "detected_at": "Date de diffusion",
    "last_seen_at": "Dernière observation",
    "source": "Source de collecte",
    "raw_metadata": "Texte brut",
}
METADATA_FIELDS = {
    "track_id": "Identifiant du titre",
    "recording_id": "Identifiant de l’enregistrement",
    "provider": "Fournisseur",
    "provider_id": "Identifiant fournisseur",
    "album": "Album",
    "duration": "Durée (secondes)",
    "isrc": "ISRC",
    "cover_url": "Pochette",
    "preview_url": "Extrait",
    "url": "Lien fournisseur",
    "spotify_url": "Lien Spotify",
    "primary_genre": "Genre principal",
    "genres": "Genres",
    "genre_source": "Source des genres",
    "release_date": "Date de sortie",
    "status": "État de correspondance",
    "manual": "Correspondance manuelle",
    "lyrics_status": "État des paroles",
    "lyrics": "Paroles sélectionnées",
    "metadata": "Toutes les métadonnées musicales (objet JSON)",
}


def available_fields(view):
    return {**(TRACK_FIELDS if view == "tracks" else PLAY_FIELDS), **METADATA_FIELDS}


@router.get("/api/export/fields")
def export_fields():
    return {view: available_fields(view) for view in ("tracks", "plays")}


class ExportFilters(ExploreFilters):
    format: Literal["csv", "json"] = "csv"
    limit: int | None = Field(None, ge=1, le=10000)
    fields: str | None = Field(None, max_length=2000)


def spotify_search(artist, title):
    return "https://open.spotify.com/search/" + quote(f"{artist or ''} {title}".strip(), safe="")


def serializable(value):
    if isinstance(value, datetime):
        return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).isoformat()
    return value


def export_row(row):
    data = {k: serializable(v) for k, v in row.items() if k != "metadata"}
    metadata = row.get("metadata") or {}
    for key in METADATA_FIELDS:
        data[key] = metadata.get(key)
    data["metadata"] = metadata
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
    allowed = available_fields(filters.view)
    columns = list(allowed) if filters.fields is None else filters.fields.split(",")
    if not columns or any(k not in allowed for k in columns) or len(set(columns)) != len(columns):
        raise HTTPException(422, "Choisissez des champs distincts disponibles pour cette vue.")
    selection = ExploreFilters(**filters.model_dump(exclude={"format", "limit", "fields"}))
    query, snapshot = result_query(db, selection)
    if filters.limit is not None:
        query = query.limit(filters.limit)
    count = db.scalar(
        select(func.count()).select_from(
            query.order_by(None).subquery()
            if filters.limit is not None
            else query.order_by(None).limit(MAX_EXPORT_ROWS + 1).subquery()
        )
    )
    if count > MAX_EXPORT_ROWS:
        raise HTTPException(
            422, "L’export dépasse 10 000 lignes. Précisez les dates ou les filtres."
        )

    def rows():
        result = db.execute(query.execution_options(yield_per=50)).mappings()
        try:
            for batch in result.partitions(50):
                enriched = attach_metadata(db, [dict(r) for r in batch])
                lyrics = (
                    dict(
                        db.execute(
                            select(TrackMetadata.id, TrackMetadata.lyrics).where(
                                TrackMetadata.id.in_([r["metadata"]["track_id"] for r in enriched])
                            )
                        ).all()
                    )
                    if "lyrics" in columns or "metadata" in columns
                    else {}
                )
                for row in enriched:
                    row["metadata"]["lyrics"] = lyrics.get(row["metadata"]["track_id"])
                    data = export_row(row)
                    yield {key: data.get(key) for key in columns}
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
                writer.writerow(
                    {
                        k: safe_cell(
                            json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                        )
                        for k, v in row.items()
                    }
                )
                yield buffer.getvalue()

    return StreamingResponse(
        content(),
        media_type="application/json" if filters.format == "json" else "text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="radio-{filters.view}.{filters.format}"',
            "Cache-Control": "no-store",
        },
    )
