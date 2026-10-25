"""Fill missing fields from compatible catalogues while preserving the chosen recording."""

import re
from copy import deepcopy
from datetime import UTC, datetime

import httpx

from app.services.catalog import (
    PROVIDER_ERRORS,
    exact_match,
    folded,
    recording_details,
    search_provider,
)

FIELDS = ("genres", "album", "release_date", "featured_artists")


def complementary_credit(selected, candidate):
    """Accept an explicit extra co-credit only with matching edition evidence.

    This exception belongs to secondary enrichment, never primary selection.
    Keep the lead artist intact: substring matches and different collaborators
    must not turn into matches just because a song has a common title.
    """
    credits = [re.split(r"\s+&\s+", row.get("artist") or "") for row in (selected, candidate)]
    short, long = sorted(credits, key=len)
    if len(short) != 1 or len(long) < 2 or not folded(short[0]):
        return False
    if folded(short[0]) != folded(long[0]):
        return False
    if any(not folded(part) for part in long):
        return False
    # Do not erase explicit guest/version qualifiers to make this exception fit.
    if folded(selected.get("title", "")) != folded(candidate.get("title", "")):
        return False
    if not selected.get("album") or not candidate.get("album"):
        return False
    if folded(selected["album"]) != folded(candidate["album"]):
        return False
    duration, other = selected.get("duration"), candidate.get("duration")
    return bool(duration and other and abs(duration - other) <= 2)


def compatible(selected, candidate):
    exact = exact_match(
        selected.get("artist"), selected.get("title", ""), candidate
    ) and exact_match(candidate.get("artist"), candidate.get("title", ""), selected)
    if not exact and not complementary_credit(selected, candidate):
        return False
    same_isrc = False
    if selected.get("isrc") and candidate.get("isrc"):
        same_isrc = selected["isrc"].upper() == candidate["isrc"].upper()
        if not same_isrc:
            return False
    duration, other = selected.get("duration"), candidate.get("duration")
    if duration and other:
        return abs(duration - other) <= 2
    return same_isrc


async def complete_missing(client, selected, previous=None, known_candidates=None):
    provider = "deezer" if selected.get("provider") == "itunes" else "itunes"
    if previous and previous.get("status") in {"matched", "not_found", "ambiguous"}:
        return deepcopy(selected), previous
    attempt = {"provider": provider, "checked_at": datetime.now(UTC).isoformat()}
    try:
        candidates = (
            known_candidates
            if known_candidates is not None
            else await search_provider(client, provider, selected.get("artist"), selected["title"])
        )
        matches = [
            c for c in candidates if c.get("provider") == provider and compatible(selected, c)
        ]
        if len(matches) != 1:
            return deepcopy(selected), {
                **attempt,
                "status": "ambiguous" if matches else "not_found",
                "filled": [],
            }
        candidate = await recording_details(client, matches[0])
        if not compatible(selected, candidate):
            return deepcopy(selected), {**attempt, "status": "not_found", "filled": []}
        result = deepcopy(selected)
        sources = dict(result.get("field_sources") or {})
        for field in FIELDS:
            if result.get(field):
                sources.setdefault(
                    field,
                    (result.get("genre_source") or result.get("provider"))
                    if field == "genres"
                    else result.get("provider"),
                )
        filled = []
        same_album = not selected.get("album") or (
            bool(candidate.get("album")) and folded(selected["album"]) == folded(candidate["album"])
        )
        for field in FIELDS:
            if field == "release_date" and not same_album:
                continue  # Do not attribute the release date of another album/edition.
            if not result.get(field) and candidate.get(field):
                result[field] = deepcopy(candidate[field])
                sources[field] = provider
                filled.append(field)
                if field == "genres":
                    result["genre_source"] = provider
        result["field_sources"] = sources
        additional = [
            s for s in result.get("additional_sources", []) if s.get("provider") != provider
        ]
        result["additional_sources"] = [
            *additional,
            {
                "provider": provider,
                "provider_id": candidate["provider_id"],
                "url": candidate.get("url"),
            },
        ][-4:]
        return result, {
            **attempt,
            "status": "matched",
            "filled": filled,
            "provider_id": candidate["provider_id"],
        }
    except PROVIDER_ERRORS:
        return deepcopy(selected), {**attempt, "status": "error", "filled": []}


async def complete_across_providers(client, selected, previous=None):
    """Explicit relaunch: preserve the recording and fill blanks from other catalogues."""
    from app.config import settings
    from app.services.providers import cover_art, search_recordings

    result = deepcopy(selected)
    attempts = deepcopy((previous or {}).get("providers") or {})
    for provider in ("deezer", "itunes", "musicbrainz", "spotify", "lastfm"):
        if provider == selected.get("provider"):
            continue
        if attempts.get(provider, {}).get("status") in {
            "matched",
            "not_found",
            "ambiguous",
            "not_configured",
            "unavailable",
        }:
            continue
        configured = {
            "spotify": bool(settings.spotify_client_id.get_secret_value())
            and bool(settings.spotify_client_secret.get_secret_value()),
            "lastfm": bool(settings.lastfm_api_key.get_secret_value()),
        }
        attempt = {
            "checked_at": datetime.now(UTC).isoformat(),
            "filled": deepcopy(attempts.get(provider, {}).get("filled", [])),
        }
        if not configured.get(provider, True):
            attempts[provider] = {**attempt, "status": "not_configured"}
            continue
        try:
            if provider in {"musicbrainz", "lastfm"}:
                page = await search_recordings(
                    client, provider, selected.get("artist"), selected["title"], limit=15
                )
                candidates = page["candidates"]
            else:
                candidates = await search_provider(
                    client, provider, selected.get("artist"), selected["title"]
                )
            matches = [
                c for c in candidates if c.get("provider") == provider and compatible(result, c)
            ]
            if len(matches) != 1:
                attempts[provider] = {**attempt, "status": "ambiguous" if matches else "not_found"}
                continue
            candidate = await recording_details(client, matches[0])
            if not compatible(result, candidate):
                attempts[provider] = {**attempt, "status": "not_found"}
                continue
            candidate = deepcopy(candidate)
            same_album = not result.get("album") or (
                bool(candidate.get("album"))
                and folded(result["album"]) == folded(candidate["album"])
            )
            artwork_failed = False
            if (
                provider == "musicbrainz"
                and same_album
                and not result.get("cover_url")
                and candidate.get("release_id")
            ):
                artwork = await cover_art(client, candidate["release_id"])
                candidate["cover_url"] = artwork.get("cover_url")
                artwork_failed = artwork["status"] == "error"
            if not same_album:
                candidate.pop("release_date", None)
                candidate.pop("cover_url", None)
            result, filled = complete_from_candidate(result, candidate)
            attempts[provider] = {
                **attempt,
                "status": "error" if artwork_failed else "matched",
                "provider_id": candidate["provider_id"],
                "filled": sorted(set(attempts.get(provider, {}).get("filled", []) + filled)),
            }
        except httpx.HTTPStatusError as error:
            attempts[provider] = {
                **attempt,
                "status": "unavailable" if error.response.status_code in {401, 403} else "error",
            }
        except PROVIDER_ERRORS:
            attempts[provider] = {**attempt, "status": "error"}
    return result, {
        "status": "error" if any(a["status"] == "error" for a in attempts.values()) else "done",
        "providers": attempts,
    }


CANDIDATE_FIELDS = (
    "album",
    "duration",
    "cover_url",
    "preview_url",
    "release_date",
    "featured_artists",
    "genres",
    "isrc",
)


def complete_from_candidate(selected, candidate):
    """An explicit admin choice fills blanks without replacing a recording."""
    from app.services.catalog import safe_url

    result = deepcopy(selected)
    filled = []
    sources = dict(result.get("field_sources") or {})
    provider = candidate.get("provider")
    for field in CANDIDATE_FIELDS:
        value = candidate.get(field)
        if field.endswith("_url"):
            value = safe_url(value)
        if not result.get(field) and value:
            result[field] = deepcopy(value)
            sources[field] = provider
            filled.append(field)
            if field == "genres":
                result["genre_source"] = candidate.get("genre_source") or provider
            if field == "preview_url":
                result["preview_source"] = {
                    "provider": provider,
                    "provider_id": candidate["provider_id"],
                }
    url = safe_url(candidate.get("url"))
    if url and provider == result.get("provider"):
        if not result.get("url") and candidate.get("provider_id") == result.get("provider_id"):
            result["url"] = url
            sources["url"] = provider
            filled.append("url")
    elif url:
        additional = deepcopy(result.get("additional_sources") or [])
        existing = next((s for s in additional if s.get("provider") == provider), None)
        if existing is None:
            additional.append(
                {
                    "provider": provider,
                    "provider_id": candidate["provider_id"],
                    "url": url,
                }
            )
            result["additional_sources"] = additional
            filled.append("additional_sources")
        elif not existing.get("url") and existing.get("provider_id") == candidate["provider_id"]:
            existing["url"] = url
            result["additional_sources"] = additional
            filled.append("additional_sources")
    if filled:
        result["field_sources"] = sources
    return result, filled


def candidate_completions(entry):
    if not entry.selected:
        return {}
    return {
        f"{c['provider']}:{c['provider_id']}": complete_from_candidate(entry.selected, c)[1]
        for c in entry.candidates
    }
