"""Read stored metadata; authenticated searches and corrections."""

from collections import OrderedDict
from datetime import UTC, datetime
from time import monotonic
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Play, Station, TrackMetadata
from app.security import require_admin
from app.services.catalog import (
    fetch_json,
    identity,
    public_metadata,
    safe_url,
    search_lyrics,
    search_provider,
)
from app.services.catalog_completion import candidate_completions, complete_from_candidate
from app.services.exclusions import canonical_exclusions, ensure_visible, exclusion_conditions

router = APIRouter(prefix="/api/catalog")
Db = Annotated[Session, Depends(get_db)]


class Search(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    provider: Literal["deezer", "itunes", "spotify", "lrclib"] = "deezer"
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
    request: Request,
    db: Db,
    artist: str = Query("", max_length=300),
    title: str = Query(..., min_length=1, max_length=500),
):
    ensure_visible(request, db, artist, title)
    entry = db.get(TrackMetadata, identity(artist, title))
    if entry is None:
        return {
            "track_id": identity(artist, title),
            "status": "pending",
            "lyrics_status": "pending",
            "lyrics": None,
            "manual": False,
        }
    ensure_visible(request, db, entry.artist, entry.title)
    return {**public_metadata(entry), "lyrics": entry.lyrics}


@router.get("/{key}/radio")
def radio_history(
    key: str,
    request: Request,
    db: Db,
    exclusions: str = Query("", max_length=12000),
    station_id: int | None = Query(None, ge=1),
):
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise HTTPException(422, "Identifiant de titre invalide.")
    entry = db.get(TrackMetadata, key)
    recording = entry or db.scalar(select(Play).where(Play.track_id == key).limit(1))
    if recording:
        ensure_visible(request, db, recording.artist, recording.title)
    try:
        exclusions = canonical_exclusions(exclusions)
    except ValueError as error:
        raise HTTPException(422, "Liste de titres masqués invalide.") from error
    where = [
        Play.track_id == key,
        *exclusion_conditions(exclusions, db.get_bind().dialect.name == "sqlite"),
    ]
    if station_id is not None:
        where.append(Play.station_id == station_id)
    total, stations, first, last = db.execute(
        select(
            func.count(),
            func.count(func.distinct(Play.station_id)),
            func.min(Play.detected_at),
            func.max(Play.detected_at),
        ).where(*where)
    ).one()
    recent = db.execute(
        select(Station.name, Play.detected_at)
        .select_from(Play)
        .join(Station)
        .where(*where)
        .order_by(Play.detected_at.desc(), Play.id.desc())
        .limit(3)
    ).all()
    return {
        "detections": total,
        "stations": stations,
        "first_at": first.replace(tzinfo=UTC) if first else None,
        "last_at": last.replace(tzinfo=UTC) if last else None,
        "recent": [
            {"station_name": name, "detected_at": at.replace(tzinfo=UTC)} for name, at in recent
        ],
    }


@router.get("/spotify-match", dependencies=[Depends(require_admin)])
async def spotify_match(
    db: Db,
    artist: str = Query("", max_length=300),
    title: str = Query(..., min_length=1, max_length=500),
):
    # Only resolve titles already in the catalogue, rather than expose a search proxy.
    entry = db.get(TrackMetadata, identity(artist, title))
    if entry is None:
        return {"status": "pending", "track": None}
    selected = entry.selected or {}
    if selected.get("provider") == "spotify" and safe_url(selected.get("url")):
        return {"status": "found", "track": selected}
    db.commit()
    from app.services.spotify import first_result

    return await first_result(artist, title)


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
    except httpx.HTTPStatusError as error:
        if payload.provider == "spotify" and error.response.status_code == 403:
            raise HTTPException(
                502,
                "Spotify refuse l’accès au catalogue pour cette application. "
                "Choisissez un autre fournisseur.",
            ) from error
        raise HTTPException(502, "Fournisseur indisponible, réessayez plus tard.") from error
    except (httpx.HTTPError, ValueError, KeyError, TypeError, TimeoutError) as error:
        raise HTTPException(502, "Fournisseur indisponible, réessayez plus tard.") from error
    entry = find(db, key)
    db.refresh(entry)
    if payload.provider == "lrclib":
        entry.lyrics_candidates = candidates
    else:
        entry.candidates = candidates
    entry.revision += 1
    # A human is reviewing this track; the worker must not overwrite the choice.
    entry.manual = True
    db.commit()
    return {
        "candidates": candidates,
        "revision": entry.revision,
        "candidate_completions": candidate_completions(entry),
    }


@router.post("/{key}/correction", dependencies=[Depends(require_admin)])
def correct(key: str, payload: Correction, db: Db):
    entry = find(db, key)
    entry.revision += 1
    if payload.automatic:
        entry.manual = False
        entry.automatic_done = False
        entry.attempts = {}
        # Retain a validated recording but retry unresolved lyrics.
        if not entry.selected:
            entry.status = "pending"
            entry.candidates = []
        if not entry.lyrics:
            entry.lyrics_status = "pending"
        entry.retry_at = datetime.now(UTC)
    elif payload.target == "lyrics":
        choices = [c for c in entry.lyrics_candidates if c["id"] == payload.candidate_id]
        if payload.candidate_id and not choices:
            raise HTTPException(422, "Paroles absentes des propositions recherchées.")
        entry.lyrics = choices[0] if choices else None
        entry.lyrics_status = "matched" if choices else "rejected"
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
        entry.lyrics_candidates = []
        entry.lyrics_status = "pending"
        entry.primary_genre = ((entry.selected or {}).get("genres") or [None])[0]
        entry.provider = entry.selected["provider"] if entry.selected else entry.provider
        entry.status = "matched" if choices else "rejected"
        entry.manual = True
    db.commit()
    from app.metrics_api import invalidate_metrics
    from app.services.explorer import invalidate_explorer

    invalidate_metrics()
    invalidate_explorer()
    return {
        **public_metadata(entry),
        "lyrics": entry.lyrics,
        "revision": entry.revision,
        "candidate_completions": candidate_completions(entry),
    }


class CandidateCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=0)


@router.post("/{key}/complete-candidate", dependencies=[Depends(require_admin)])
def complete_candidate(key: str, payload: CandidateCompletion, db: Db):
    entry = find(db, key)
    db.refresh(entry)
    if entry.revision != payload.revision:
        raise HTTPException(
            409, "La fiche a changé. Rouvrez-la avant de compléter les informations."
        )
    if not entry.selected:
        raise HTTPException(409, "Choisissez une version avant de compléter ses informations.")
    candidate = next(
        (
            c
            for c in entry.candidates
            if f"{c['provider']}:{c['provider_id']}" == payload.candidate_id
        ),
        None,
    )
    if candidate is None:
        raise HTTPException(422, "Morceau absent des propositions recherchées.")
    completed, filled = complete_from_candidate(entry.selected, candidate)
    if filled:
        changed = db.execute(
            update(TrackMetadata)
            .where(TrackMetadata.id == key, TrackMetadata.revision == payload.revision)
            .values(
                selected=completed,
                revision=payload.revision + 1,
                manual=True,
                primary_genre=(completed.get("genres") or [None])[0],
            )
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            db.rollback()
            raise HTTPException(409, "La fiche a changé. Rouvrez-la.")
        db.commit()
        db.refresh(entry)
        from app.metrics_api import invalidate_metrics
        from app.services.explorer import invalidate_explorer

        invalidate_metrics()
        invalidate_explorer()
    return {
        **public_metadata(entry),
        "revision": entry.revision,
        "candidate_completions": candidate_completions(entry),
        "completion": {"status": "matched" if filled else "unchanged", "filled": filled},
    }


@router.post("/{key}/complete", dependencies=[Depends(require_admin)])
async def complete_metadata(key: str, db: Db):
    from copy import deepcopy

    from app.metrics_api import invalidate_metrics
    from app.services.catalog_completion import complete_missing
    from app.services.explorer import invalidate_explorer

    entry = find(db, key)
    if not entry.selected:
        raise HTTPException(409, "Validez un enregistrement avant de compléter ses informations.")
    selected, revision = deepcopy(entry.selected), entry.revision
    db.commit()
    async with httpx.AsyncClient(timeout=12, headers={"User-Agent": "RadioTracker/0.1"}) as client:
        completed, attempt = await complete_missing(client, selected)
    entry = find(db, key)
    db.refresh(entry)
    if entry.revision != revision:
        raise HTTPException(409, "La fiche a changé pendant la recherche. Rouvrez-la.")
    attempts = {**entry.attempts, "complement": attempt}
    changed = db.execute(
        update(TrackMetadata)
        .where(TrackMetadata.id == key, TrackMetadata.revision == revision)
        .values(
            selected=completed,
            attempts=attempts,
            revision=revision + 1,
            primary_genre=(completed.get("genres") or [None])[0],
        )
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "La fiche a changé pendant la recherche. Rouvrez-la.")
    db.commit()
    db.refresh(entry)
    invalidate_metrics()
    invalidate_explorer()
    return {**public_metadata(entry), "completion": attempt}


_preview_cache = OrderedDict()


@router.get("/{key}/preview")
async def preview(key: str, request: Request, db: Db):
    entry = find(db, key)
    ensure_visible(request, db, entry.artist, entry.title)
    selected = dict(entry.selected or {})
    db.commit()
    if not selected:
        raise HTTPException(404, "Aucune version validée.")
    return await recording_preview(selected)


@router.get("/{key}/candidates/{candidate_id}/preview", dependencies=[Depends(require_admin)])
async def candidate_preview(key: str, candidate_id: str, db: Db):
    entry = find(db, key)
    candidate = next(
        (
            dict(c)
            for c in entry.candidates
            if f"{c['provider']}:{c['provider_id']}" == candidate_id
        ),
        None,
    )
    if candidate is None:
        raise HTTPException(404, "Morceau absent des propositions recherchées.")
    db.commit()  # Do not hold a database connection while refreshing the URL.
    return await recording_preview(candidate)


async def recording_preview(selected):
    preview_provider = (selected.get("field_sources") or {}).get("preview_url")
    # A missing primary excerpt can have been supplied by a different catalogue.
    external_preview = preview_provider and preview_provider != selected["provider"]
    cache_key = (
        selected["provider"],
        selected["provider_id"],
        selected.get("preview_url") if external_preview else None,
    )
    cached = _preview_cache.get(cache_key)
    url = cached[1] if cached and cached[0] > monotonic() else None
    if not url:
        if (
            selected["provider"] == "deezer"
            and selected["provider_id"].isdigit()
            and not external_preview
        ):
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
