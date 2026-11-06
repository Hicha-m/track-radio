"""Shared JSON catalogues for Jinja and the browser; French is the default."""

import json
import re
from pathlib import Path

CATALOGUES = {
    lang: json.loads((Path(__file__).parent / "locales" / f"{lang}.json").read_text())
    for lang in ("fr", "en")
}
SOURCE_KEYS = {value: key for key, value in CATALOGUES["fr"].items()}


def language(request):
    requested = request.query_params.get("lang")
    return (
        requested
        if requested in CATALOGUES
        else request.cookies.get("radio_language", "fr")
        if request.cookies.get("radio_language") in CATALOGUES
        else "fr"
    )


def translate(key, lang="fr", **params):
    key = SOURCE_KEYS.get(key, key)
    value = CATALOGUES.get(lang, CATALOGUES["fr"]).get(key, CATALOGUES["fr"].get(key, key))
    return re.sub(r"\{(\w+)\}", lambda m: str(params.get(m[1], m[0])), value)
