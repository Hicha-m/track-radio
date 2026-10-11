import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.models import Station


@dataclass(frozen=True)
class TrackMetadata:
    artist: str | None
    title: str
    raw: str
    source: str


def split_title(raw: str) -> tuple[str | None, str]:
    value = " ".join(raw.replace("\x00", "").split()).strip(" -")
    value = re.sub(r"\s*§\d+$", "", value).strip()
    if " - " in value:
        artist, title = value.split(" - ", 1)
        return artist.strip() or None, title.strip()
    return None, value


def parse_icy_block(block: bytes) -> str | None:
    text = block.rstrip(b"\x00").decode("utf-8", errors="replace")
    match = re.search(r"StreamTitle='(.*?)';", text)
    return match.group(1).strip() if match and match.group(1).strip() else None


async def fetch_icy(station: Station, timeout: float) -> TrackMetadata | None:
    headers = {"Icy-MetaData": "1", "User-Agent": "RadioTracker/0.1"}
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        async with client.stream("GET", station.stream_url, headers=headers) as response:
            response.raise_for_status()
            metaint_value = response.headers.get("icy-metaint")
            if not metaint_value:
                return None
            metaint = int(metaint_value)
            data = bytearray()
            needed = metaint + 1
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) >= needed:
                    length = data[metaint] * 16
                    needed = metaint + 1 + length
                    if len(data) >= needed:
                        raw = parse_icy_block(bytes(data[metaint + 1 : needed]))
                        if not raw:
                            return None
                        artist, title = split_title(raw)
                        return TrackMetadata(artist, title, raw, "icy")
    return None


def _json_path(payload: Any, path: str | None) -> Any:
    current = payload
    for part in (path or "").split("."):
        if not part:
            continue
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


async def fetch_json(station: Station, timeout: float) -> TrackMetadata | None:
    if not station.metadata_url or not station.json_title_path:
        return None
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        response = await client.get(
            station.metadata_url, headers={"User-Agent": "RadioTracker/0.1"}
        )
        response.raise_for_status()
        payload = response.json()
    title = str(_json_path(payload, station.json_title_path)).strip()
    artist_value = (
        _json_path(payload, station.json_artist_path) if station.json_artist_path else None
    )
    artist = str(artist_value).strip() if artist_value else None
    if not title:
        return None
    raw = f"{artist} - {title}" if artist else title
    return TrackMetadata(artist, title, raw, "json")


async def fetch_metadata(station: Station, timeout: float) -> TrackMetadata | None:
    if station.source_type == "json":
        return await fetch_json(station, timeout)
    return await fetch_icy(station, timeout)

