import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from app.config import settings
from app.models import Station
from app.services.html_sources import HTML_ADAPTERS, HtmlConfig, parse_funradio, parse_html
from app.services.network import metadata_client


@dataclass(frozen=True)
class TrackMetadata:
    artist: str | None
    title: str
    raw: str
    source: str
    source_started_ms: int | None = None


def split_title(raw: str) -> tuple[str | None, str]:
    value = " ".join(raw.replace("\x00", "").split()).strip(" -")
    value = re.sub(r"\s*§\d+$", "", value).strip()
    if " - " in value:
        artist, title = value.split(" - ", 1)
        return artist.strip() or None, title.strip()
    if " • " in value:
        artist, title = value.split(" • ", 1)
        return artist.strip() or None, title.strip()
    return None, value


def parse_icy_block(block: bytes) -> str | None:
    text = block.rstrip(b"\x00").decode("utf-8", errors="replace")
    match = re.search(r"StreamTitle='(.*?)';", text)
    return match.group(1).strip() if match and match.group(1).strip() else None


async def fetch_icy(station: Station, client: httpx.AsyncClient) -> TrackMetadata | None:
    headers = {"Icy-MetaData": "1", "User-Agent": "RadioTracker/0.1"}
    async with client.stream("GET", station.stream_url, headers=headers) as response:
        response.raise_for_status()
        check_encoding(response)
        metaint_value = response.headers.get("icy-metaint")
        if not metaint_value:
            return None
        metaint = int(metaint_value)
        if not 1 <= metaint <= settings.max_icy_metaint:
            raise ValueError("Intervalle ICY trop grand ou invalide.")
        data = bytearray()
        needed = metaint + 1
        async for chunk in response.aiter_raw(chunk_size=4096):
            if len(data) + len(chunk) > settings.max_response_bytes:
                raise ValueError("Réponse ICY trop volumineuse.")
            data.extend(chunk)
            if len(data) >= needed:
                length = data[metaint] * 16
                needed = metaint + 1 + length
                if len(data) >= needed:
                    raw = parse_icy_block(bytes(data[metaint + 1 : needed]))
                    if not raw:
                        return None
                    artist, title = split_title(raw)
                    return checked_metadata(artist, title, raw, "icy")
    return None


def _json_path(payload: Any, path: str | None) -> Any:
    current = payload
    for part in (path or "").split("."):
        if not part:
            continue
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


async def fetch_json(station: Station, client: httpx.AsyncClient) -> TrackMetadata | None:
    if not station.metadata_url or not station.json_title_path:
        return None
    url = station.metadata_url.replace("{now_ms}", str(int(datetime.now(UTC).timestamp() * 1000)))
    content = await read_bounded(client, url)
    try:
        payload = json.loads(content)
    except UnicodeDecodeError:
        payload = json.loads(content.decode("cp1252"))
    title_value = _json_path(payload, station.json_title_path)
    if not isinstance(title_value, str):
        return None
    title = title_value.strip()
    artist_value = (
        _json_path(payload, station.json_artist_path) if station.json_artist_path else None
    )
    artist = str(artist_value).strip() if artist_value else None
    if not title:
        return None
    if not artist and " • " in title:
        artist, title = split_title(title)
    raw = f"{artist} - {title}" if artist else title
    checked = checked_metadata(artist, title, raw, "json")
    # Radio France exposes the event time independently of the display label.
    # Keep it in durable raw metadata, as for the Fun Radio adapter.
    if station.json_started_path or urlsplit(station.metadata_url).hostname == "api.radiofrance.fr":
        try:
            value = _json_path(payload, station.json_started_path or "now.startTime")
        except (KeyError, IndexError, TypeError):
            value = None
        start = radiofrance_start(value)
        if start is not None and start > int(datetime.now(UTC).timestamp() * 1000):
            return None
        raw = json.dumps({"start": start, "payload": payload}, ensure_ascii=False)
        return TrackMetadata(checked.artist, checked.title, raw, "json", start)
    return checked


def radiofrance_start(value) -> int | None:
    """Accept epoch seconds/milliseconds or an explicitly zoned ISO timestamp."""
    try:
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                date = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return int(date.timestamp() * 1000) if date.tzinfo else None
        if type(value) in (int, float) and value > 0:
            return int(value if value >= 100_000_000_000 else value * 1000)
    except (ValueError, OverflowError):
        pass
    return None


async def fetch_html(station: Station, client: httpx.AsyncClient) -> TrackMetadata | None:
    if not station.metadata_url:
        return None
    # Compatibilité des bases antérieures à la migration/configuration HTML explicite.
    legacy_funradio = (
        station.metadata_url.rstrip("/") == "https://www.funradio.fr/quel-est-ce-titre"
    )
    options = HtmlConfig.model_validate(
        station.html or ({"adapter": "funradio"} if legacy_funradio else {})
    )
    adapter = HTML_ADAPTERS[options.adapter]
    now = datetime.now(ZoneInfo("Europe/Paris"))
    url = adapter.url(station.metadata_url, now)
    content = await read_bounded(client, url)
    if options.adapter == "funradio":
        info = await asyncio.to_thread(parse_funradio, content, now)
        if info is None:
            return None
        raw = json.dumps(info, ensure_ascii=False)
        checked = checked_metadata(info["singer"], info["title"], raw, "html")
        return TrackMetadata(checked.artist, checked.title, raw, "html", info["start"])
    result = await asyncio.to_thread(parse_html, adapter, content, options)
    if result is None:
        return None
    artist, title = result
    raw = f"{artist} - {title}" if artist else title
    return checked_metadata(artist, title, raw, "html")


def checked_metadata(artist: str | None, title: str, raw: str, source: str) -> TrackMetadata:
    if not title or len(title) > 500 or (artist and len(artist) > 300):
        raise ValueError("Métadonnées invalides ou trop longues.")
    return TrackMetadata(artist, title, raw, source)


def check_encoding(response: httpx.Response):
    # Refuse compression rather than risking a decompression bomb before size checks.
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise ValueError("Compression HTTP non prise en charge pour les métadonnées.")


async def read_bounded(client: httpx.AsyncClient, url: str) -> bytes:
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        check_encoding(response)
        length = response.headers.get("content-length")
        if length and int(length) > settings.max_response_bytes:
            raise ValueError("Réponse distante trop volumineuse.")
        data = bytearray()
        async for chunk in response.aiter_raw(chunk_size=4096):
            if len(data) + len(chunk) > settings.max_response_bytes:
                raise ValueError("Réponse distante trop volumineuse.")
            data.extend(chunk)
        return bytes(data)


async def fetch_metadata(
    station: Station,
    timeout: float,
    client: httpx.AsyncClient | None = None,
) -> TrackMetadata | None:
    if client is None:
        async with metadata_client() as owned_client:
            return await fetch_metadata(station, timeout, owned_client)
    async with asyncio.timeout(timeout):
        if station.source_type == "json":
            return await fetch_json(station, client)
        if station.source_type == "html":
            return await fetch_html(station, client)
        return await fetch_icy(station, client)
