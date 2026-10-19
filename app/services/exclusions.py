"""User supplied exact recording exclusions, applied before ranking and pagination."""

import json

from pydantic import BaseModel, Field, TypeAdapter, field_validator
from sqlalchemy import and_, func, or_

from app.models import Play
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
    unique = {
        (
            normalize_search(t.artist or "").replace("’", "'"),
            normalize_search(t.title).replace("’", "'"),
        )
        for t in tracks
    }
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
    if not value:
        return []
    fold = func.fold_text if unicode else func.lower
    return [
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
