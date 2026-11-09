"""Optional matching artwork through the same public signed requests as myTuner's site.

The provider's public signer runs without Python callbacks, filesystem or networking.
Neither its key nor generated authorization values are persisted or logged.
"""

import asyncio
import re
from time import monotonic, time

import httpx
import quickjs

from app.services.catalog import exact_match, fetch_json, safe_url
from app.services.collection_diagnostics import trace_context
from app.services.metadata import read_bounded, split_title

ASSETS = "https://cdn.regional-radios.app-mind.com/static/assets/"
CRYPTO_URL = ASSETS + "scripts/vendor/cryptoJS.min.js"
SIGNER_URL = ASSETS + "scripts/obfuscated/metadata-helper-fr.js"
API = "https://metadata-api.mytuner.mobi/api/v1/metadata-api/web/metadata"
RADIOS = {
    "rmc": 408810,
    "rtl": 408797,
    "france-info": 410589,
    "europe1": 408799,
    "france-inter": 402375,
    "nostalgie": 410610,
    "cherie-fm": 410571,
    "rfm": 408802,
    "rtl2": 407798,
    "nrj": 410619,
    "skyrock": 412056,
    "fun-radio": 408796,
    "rire-et-chansons": 410635,
    "france-culture": 402374,
    "fip": 402373,
    "radio-classique": 399420,
    "france-bleu-provence": 402368,
    "impact-annees-80": 396730,
    "bfm-radio": 496571,
    "europe2": 408813,
    "m-radio": 402180,
    "sud-radio": 516928,
    "skyrock-klassiks": 454058,
    "chante-france": 407191,
    "radio-nova": 399424,
    "jazz-radio": 399342,
    "latina": 406930,
    "fg": 400265,
    "maritima-radio": 407073,
    "kiss-fm": 412372,
}
_assets = None
_assets_until = 0
_assets_lock = asyncio.Lock()
_observations = {}


def authorization(crypto, signer, radio_id, timestamp):
    # A fresh isolated runtime per invocation, confined to this worker thread.
    context = quickjs.Context()
    context.set_memory_limit(8 * 1024 * 1024)
    context.set_max_stack_size(256 * 1024)
    context.set_time_limit(0.5)
    context.eval("var window = globalThis;")
    context.eval(crypto)
    context.eval(signer)
    context.set("radioId", int(radio_id))
    # Context.set maps Python ints to int32; JS timestamps need an exact double.
    context.set("requestTime", float(timestamp))
    value = context.eval('new DG(radioId).execute(requestTime, "metadata")')
    if not isinstance(value, str) or not re.fullmatch(
        r"HMAC radiofrance_web:[A-Za-z0-9_+/-]{43}=?", value
    ):
        raise ValueError("Invalid provider authorization")
    return value


def matching_cover(payload, track):
    if (
        not isinstance(payload, dict)
        or payload.get("error_code") != 0
        or payload.get("no_metadata_available") is not False
    ):
        return None
    item = payload.get("radio_metadata")
    if not isinstance(item, dict) or not isinstance(item.get("metadata"), str):
        return None
    artist, title = split_title(item["metadata"])
    if not artist or len(artist) > 300 or len(title) > 500:
        return None
    if not exact_match(track.artist, track.title, {"artist": artist, "title": title}):
        return None
    return safe_url(item.get("artwork_url_large")) or safe_url(item.get("artwork_url_small"))


async def public_assets(client):
    global _assets, _assets_until
    async with _assets_lock:
        if monotonic() >= _assets_until:
            _assets = None
            _assets_until = monotonic() + 60
            crypto = await read_bounded(client, CRYPTO_URL)
            signer = await read_bounded(client, SIGNER_URL)
            _assets = (crypto.decode("utf-8"), signer.decode("utf-8"))
            _assets_until = monotonic() + 6 * 3600
    if _assets is None:
        raise ValueError("Provider signer unavailable")
    return _assets


async def cover_for(station, track, client):
    radio_id = RADIOS.get(station.slug)
    if radio_id is None or not track.is_music:
        return None
    # Cache only one sanitized observation per configured radio, never a raw payload.
    cached = _observations.get(radio_id)
    if cached and monotonic() < cached["until"]:
        return cached["cover"] if cached["identity"] == (track.artist, track.title) else None
    _observations[radio_id] = {
        "until": monotonic() + 30,
        "cover": None,
        "identity": (track.artist, track.title),
    }
    token = trace_context.set(None)
    try:
        async with asyncio.timeout(8):
            crypto, signer = await public_assets(client)
            timestamp = int(time() * 1000)
            header = await asyncio.to_thread(authorization, crypto, signer, radio_id, timestamp)
            params = {"app_codename": "radiofrance_web", "radio_id": radio_id, "time": timestamp}
            payload = await fetch_json(
                client,
                API,
                params=params,
                headers={
                    "Authorization": header,
                    "Origin": "https://www.radio-en-ligne.fr",
                    "Referer": "https://www.radio-en-ligne.fr/",
                },
            )
            cover = matching_cover(payload, track)
            _observations[radio_id]["cover"] = cover
            return cover
    except (httpx.HTTPError, ValueError, KeyError, TypeError, TimeoutError, quickjs.JSException):
        return None  # No exception text: provider code can contain confidential values.
    finally:
        trace_context.reset(token)
