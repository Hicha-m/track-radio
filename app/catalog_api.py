"""Read stored metadata; authenticated searches and corrections."""

from collections import OrderedDict
from datetime import UTC, datetime
from time import monotonic
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import TrackMetadata
from app.security import require_admin
from app.services.catalog import (
    fetch_json,
    identity,
    public_metadata,
    safe_url,
    search_lyrics,
    search_provider,
)

router = APIRouter(prefix="/api/catalog")
Db = Annotated[Session, Depends(get_db)]


class Search(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    provider: Literal["deezer", "itunes", "lrclib"] = "deezer"
    artist: str = Field("", max_length=300)
    title: str = Field(min_length=1, max_length=500)


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["recording", "lyrics"] = "recording"
    candidate_id: str | None = Field(None, max_length=100)
    automatic: bool = False


def find(db, key):
    entry = db.get(TrackMetadata, key)
    if entry is None:
        raise HTTPException(404, "Musique encore en attente de collecte.")
    return entry


@router.get("")
def metadata(
    db: Db,
    artist: str = Query("", max_length=300),
    title: str = Query(..., min_length=1, max_length=500),
):
    entry = db.get(TrackMetadata, identity(artist, title))
    if entry is None:
        return {
            "track_id": identity(artist, title),
            "status": "pending",
            "candidates": [],
            "lyrics": None,
            "manual": False,
        }
    return {**public_metadata(entry), "candidates": entry.candidates, "lyrics": entry.lyrics}


@router.post("/{key}/search", dependencies=[Depends(require_admin)])
async def search(key: str, payload: Search, db: Db):
    find(db, key)
    db.commit()  # Release the connection before provider I/O.
    try:
        async with httpx.AsyncClient(
            timeout=12, headers={"User-Agent": "RadioTracker/0.1"}
        ) as client:
            if payload.provider == "lrclib":
                candidates = await search_lyrics(client, payload.artist, payload.title)
            else:
                candidates = await search_provider(
                    client, payload.provider, payload.artist, payload.title
                )
    except (httpx.HTTPError, ValueError, KeyError, TypeError, TimeoutError) as error:
        raise HTTPException(502, "Fournisseur indisponible, réessayez plus tard.") from error
    entry = find(db, key)
    db.refresh(entry)
    if payload.provider == "lrclib":
        entry.lyrics_candidates = candidates
    else:
        entry.candidates = candidates
    # A human is reviewing this track; the worker must not overwrite the choice.
    entry.manual = True
    db.commit()
    return {"candidates": candidates}


@router.post("/{key}/correction", dependencies=[Depends(require_admin)])
def correct(key: str, payload: Correction, db: Db):
    entry = find(db, key)
    if payload.automatic:
        entry.manual = False
        entry.retry_at = datetime.now(UTC)
    elif payload.target == "lyrics":
        choices = [c for c in entry.lyrics_candidates if c["id"] == payload.candidate_id]
        if payload.candidate_id and not choices:
            raise HTTPException(422, "Paroles absentes des propositions recherchées.")
        entry.lyrics = choices[0] if choices else None
        entry.manual = True
    else:
        choices = [
            c
            for c in entry.candidates
            if f"{c['provider']}:{c['provider_id']}" == payload.candidate_id
        ]
        if payload.candidate_id and not choices:
            raise HTTPException(422, "Morceau absent des propositions recherchées.")
        entry.selected = choices[0] if choices else None
        entry.lyrics = None  # Lyrics for a different recording must be checked again.
        entry.provider = entry.selected["provider"] if entry.selected else entry.provider
        entry.status = "matched" if choices else "rejected"
        entry.manual = True
    db.commit()
    return {**public_metadata(entry), "lyrics": entry.lyrics}


_preview_cache = OrderedDict()


@router.get("/{key}/preview")
async def preview(key: str, db: Db):
    entry = find(db, key)
    selected = dict(entry.selected or {})
    db.commit()
    if not selected:
        raise HTTPException(404, "Aucune version validée.")
    cache_key = (selected["provider"], selected["provider_id"])
    cached = _preview_cache.get(cache_key)
    url = cached[1] if cached and cached[0] > monotonic() else None
    if not url:
        if selected["provider"] == "deezer" and selected["provider_id"].isdigit():
            try:
                async with httpx.AsyncClient(timeout=12) as client:
                    data = await fetch_json(
                        client, f"https://api.deezer.com/track/{selected['provider_id']}"
                    )
                url = safe_url(data.get("preview"))
            except (httpx.HTTPError, ValueError, TimeoutError) as error:
                raise HTTPException(502, "Extrait temporairement indisponible.") from error
        else:
            url = safe_url(selected.get("preview_url"))
        if not url:
            raise HTTPException(404, "Aucun extrait pour cette version.")
        _preview_cache[cache_key] = (monotonic() + 60, url)
        _preview_cache.move_to_end(cache_key)
        while len(_preview_cache) > 128:
            _preview_cache.popitem(last=False)
    return RedirectResponse(url, headers={"Cache-Control": "no-store"})
