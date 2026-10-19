"""Stable identity shared by collection, migrations and the catalogue."""

import hashlib
import json

from app.services.search_text import normalize_search


def identity(artist, title):
    # Preserve version and featuring information in our identity; never merge broadcasts.
    values = [" ".join(normalize_search(v).split()) for v in (artist, title)]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()
