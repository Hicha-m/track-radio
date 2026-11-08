"""Bounded, filtered playlist preparation and portable exports; no account writes."""

import csv
import io
import json
import re
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.export_api import safe_cell, spotify_search
from app.models import GlobalExclusion, Play, YouTubeMatch
from app.services.catalog import attach_metadata
from app.services.exclusions import ExclusionFilters, normalized_pair
from app.services.explorer import ExploreFilters, result_query

router = APIRouter(prefix="/api/playlists")
Db = Annotated[Session, Depends(get_db)]
Filters = Annotated[ExclusionFilters, Query()]


class PlaylistFilters(ExclusionFilters):
    model_config = ConfigDict(extra="forbid")
    station_ids: str = Field("", max_length=2000)
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(20, ge=1, le=50)
    sort: Literal["frequency", "newest", "oldest", "artist", "title"] = "frequency"

    @model_validator(mode="after")
    def valid_scope(self):
        scope = ExploreFilters(**self.model_dump(exclude={"limit"}))
        self.station_ids, self.since, self.until = scope.station_ids, scope.since, scope.until
        return self


@router.get("/selection")
def selection(db: Db, filters: Annotated[PlaylistFilters, Query()]):
    scope = ExploreFilters(**filters.model_dump(exclude={"limit"}), view="tracks")
    query, snapshot = result_query(db, scope)
    items = attach_metadata(
        db, [dict(row) for row in db.execute(query.limit(filters.limit)).mappings()]
    )
    # Return only the fields the composer needs, never lyrics or raw detections.
    rows = [
        {
            "track_id": row["metadata"]["track_id"],
            "artist": row["artist"],
            "title": row["title"],
            "plays": row["plays"],
        }
        for row in items
    ]
    return JSONResponse(
        {"items": rows, "snapshot_id": snapshot}, headers={"Cache-Control": "no-store"}
    )


class PlaylistItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    track_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    video_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{11}$")


class PlaylistExport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[PlaylistItem] = Field(min_length=1, max_length=50)
    destination: Literal["youtube", "spotify"] = "youtube"
    format: Literal["json", "csv"] = "json"


def manifest(db, body, exclusions):
    hidden = {
        normalized_pair(row.get("artist"), row["title"]) for row in json.loads(exclusions or "[]")
    } | {normalized_pair(row.artist, row.title) for row in db.scalars(select(GlobalExclusion))}
    keys = {item.track_id for item in body.items}
    # Check every spelling of an identity; one excluded alias hides the whole recording.
    representatives = {}
    for row in db.execute(
        select(Play.track_id, Play.artist, Play.title)
        .where(Play.track_id.in_(keys))
        .distinct()
        .order_by(Play.track_id, Play.title, Play.artist)
    ):
        representatives.setdefault(row.track_id, row)
        if normalized_pair(row.artist, row.title) in hidden:
            representatives[row.track_id] = None
    matches = {
        match.track_id: match
        for match in db.scalars(select(YouTubeMatch).where(YouTubeMatch.track_id.in_(keys)))
    }
    visible = []
    seen_tracks, seen_videos = set(), set()
    skipped = 0
    for item in body.items:
        row = representatives.get(item.track_id)
        if row is None or item.track_id in seen_tracks:
            skipped += 1
            continue
        seen_tracks.add(item.track_id)
        match = matches.get(item.track_id)
        candidates = (
            []
            if match is None or match.status == "rejected"
            else [*(match.candidates or []), *([match.selected] if match.selected else [])]
        )
        video = next((v for v in candidates if v.get("id") == item.video_id), None)
        if video and item.video_id in seen_videos:
            video = None
        if video:
            seen_videos.add(item.video_id)
        term = quote(f"{row.artist or ''} {row.title}".strip(), safe="")
        visible.append(
            {
                "track_id": item.track_id,
                "artist": row.artist,
                "title": row.title,
                "video": video,
                "status": "ready" if video else "unresolved",
                "url": (
                    f"https://www.youtube.com/watch?v={item.video_id}"
                    if video
                    else f"https://www.youtube.com/results?search_query={term}"
                )
                if body.destination == "youtube"
                else spotify_search(row.artist, row.title),
            }
        )
    if body.destination == "spotify":
        enriched = attach_metadata(db, visible)
        for row, enriched_row in zip(visible, enriched, strict=True):
            metadata = enriched_row["metadata"]
            url = metadata.get("url") or ""
            exact = metadata.get("provider") == "spotify" and re.fullmatch(
                r"https://open\.spotify\.com/track/[A-Za-z0-9]{22}", url
            )
            row["url"] = url if exact else spotify_search(row["artist"], row["title"])
            row["status"] = "ready" if exact else "unresolved"
            row["video"] = None
    return {"destination": body.destination, "items": visible, "skipped": skipped}


@router.post("/export")
def export_playlist(db: Db, body: PlaylistExport, filters: Filters):
    data = manifest(db, body, filters.exclusions)
    headers = {"Cache-Control": "no-store", "X-Playlist-Skipped": str(data["skipped"])}
    if body.format == "json":
        return JSONResponse(data, headers=headers)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["position", "artist", "title", "status", "url"])
    for position, row in enumerate(data["items"], 1):
        writer.writerow(
            [position, *[safe_cell(row[key]) for key in ("artist", "title", "status", "url")]]
        )
    headers["Content-Disposition"] = f'attachment; filename="radio-playlist-{body.destination}.csv"'
    return Response(buffer.getvalue().encode("utf-8-sig"), media_type="text/csv", headers=headers)
