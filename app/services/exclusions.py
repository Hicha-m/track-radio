"""User supplied exact recording exclusions, applied before ranking and pagination."""

import hashlib
import json

from pydantic import BaseModel, Field, TypeAdapter, field_validator
from sqlalchemy import and_, func, or_, select

from app.models import GlobalExclusion, Play
from app.services.search_text import normalize_search


class HiddenTrack(BaseModel):
    artist: str | None = Field(None, max_length=300)
    title: str = Field(min_length=1, max_length=500)


_tracks = TypeAdapter(list[HiddenTrack])


def canonical_exclusions(value):
    if not value:
        return ""
    tracks = _tracks.validate_json(value)
    if len(tracks) > 50:
        raise ValueError("Limiter la liste à 50 titres masqués.")
    unique = {normalized_pair(t.artist, t.title) for t in tracks}
    return (
        json.dumps(
            [{"artist": a or None, "title": t} for a, t in sorted(unique)],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if unique
        else ""
    )


class ExclusionFilters(BaseModel):
    exclusions: str = Field("", max_length=12000)

    @field_validator("exclusions")
    @classmethod
    def valid_exclusions(cls, value):
        return canonical_exclusions(value)


def exclusion_conditions(value, unicode=True):
    conditions = [global_condition(unicode=unicode)]
    if not value:
        return conditions
    fold = func.fold_text if unicode else func.lower
    return conditions + [
        ~or_(
            *[
                and_(
                    func.replace(fold(func.coalesce(Play.artist, "")), "’", "'")
                    == (t["artist"] or ""),
                    func.replace(fold(Play.title), "’", "'") == t["title"],
                )
                for t in json.loads(value)
            ]
        )
    ]


def normalized_pair(artist, title):
    return tuple(normalize_search(v or "").replace("’", "'") for v in (artist, title))


def exclusion_id(artist, title):
    return hashlib.sha256(
        json.dumps(normalized_pair(artist, title), ensure_ascii=False).encode()
    ).hexdigest()


def global_revision(db):
    # Content addressing works across workers and survives restarts.
    keys = db.scalars(select(GlobalExclusion.id).order_by(GlobalExclusion.id)).all()
    return hashlib.sha256("".join(keys).encode()).hexdigest()


def global_condition(artist=Play.artist, title=Play.title, unicode=True):
    fold = func.fold_text if unicode else func.lower
    return (
        ~select(GlobalExclusion.id)
        .where(
            GlobalExclusion.artist_normalized
            == func.replace(fold(func.coalesce(artist, "")), "’", "'"),
            GlobalExclusion.title_normalized == func.replace(fold(title), "’", "'"),
        )
        .exists()
    )


def ensure_visible(request, db, artist, title):
    """Direct public links cannot bypass hiding; authenticated admins can inspect."""
    from fastapi import HTTPException

    from app.admin_sessions import require_admin

    if db.get(GlobalExclusion, exclusion_id(artist, title)) is not None:
        try:
            require_admin(request, db)
        except HTTPException as error:
            raise HTTPException(404, "Titre introuvable.") from error
