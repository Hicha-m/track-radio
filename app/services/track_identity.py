"""Stable identity shared by collection, migrations and the catalogue."""

import hashlib
import json

from app.services.search_text import normalize_search


def identity(artist, title):
    # Preserve version and featuring information in our identity; never merge broadcasts.
    values = [" ".join(normalize_search(v).split()) for v in (artist, title)]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def grouping_columns():
    """Group confirmed identities; keep unidentified legacy rows separate by raw pair."""
    from sqlalchemy import case

    from app.models import Play

    return (
        # Put fallback expressions first so SQLite can scan a covering index
        # instead of walking track_id with a table lookup for every detection.
        case((Play.track_id.is_(None), Play.artist)),
        case((Play.track_id.is_(None), Play.title)),
        Play.track_id,
    )
