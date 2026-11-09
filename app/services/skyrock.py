"""Strict per-radio schedule selection; never retain provider/user payloads."""

import asyncio
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from math import ceil
from time import monotonic
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary

import httpx

ENDPOINTS = {
    "https://skyrock.fm/api/v3/player/onair/super",
    "https://skyrock.fm/api/v3/player/onair",
}
NATIONAL_STREAM = "https://icecast.skyrock.net/s/natio_mp3_128k"
SUPER_ENDPOINT = "https://skyrock.fm/api/v3/player/onair/super"


def timestamp(info, name):
    value = info[name + "_ts"]
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit():
        raise ValueError("Invalid event clock")
    epoch = int(value)
    date = datetime.fromisoformat(info[name])
    if date.tzinfo is None or date.timestamp() != epoch:
        raise ValueError("Inconsistent event clock")
    return epoch


def parse_schedule(payload, *, super_endpoint=True, now=None, radio="natio"):
    from app.services.metadata import TrackMetadata, checked_metadata

    now = (now or datetime.now(UTC)).timestamp()
    if not isinstance(payload, dict) or type(payload.get("ts")) is not int:
        raise ValueError("Missing provider clock")
    clock = payload["ts"]
    if not -5 <= now - clock <= 60:
        return None
    schedule = (
        payload.get("radios", {}).get(radio, {}).get("schedule")
        if super_endpoint
        else payload.get("schedule")
    )
    if not isinstance(schedule, list) or len(schedule) > 100:
        raise ValueError("Invalid radio schedule")
    current = {}
    for entry in schedule:
        if not isinstance(entry, dict):
            raise ValueError("Invalid schedule entry")
        if entry.get("type") != "record":
            continue
        info = entry["info"]
        start, end = timestamp(info, "start"), timestamp(info, "end")
        if not 0 < end - start <= 7200:
            raise ValueError("Invalid event duration")
        if not (start <= clock < end and start <= now < end):
            continue
        uid = info["uid"]
        if type(uid) is not int or uid <= 0:
            raise ValueError("Invalid event identity")
        artists = entry["artists"]
        if not isinstance(artists, list) or not artists or len(artists) > 20:
            raise ValueError("Invalid artists")
        names = [artist["name"].strip() for artist in artists]
        if not all(names) or not isinstance(info.get("title"), str):
            raise ValueError("Missing musical label")
        artist, title = ", ".join(names), info["title"].strip()
        checked_metadata(artist, title, "", "json")
        event_id = f"skyrock:{uid}"
        raw = json.dumps({"start": start * 1000, "event_id": event_id}, ensure_ascii=False)
        music = not (artist.casefold() == "skyrock" and title.casefold() == "hit skyrock")
        item = TrackMetadata(artist, title, raw, "json", start * 1000, event_id, music)
        if event_id in current and current[event_id] != item:
            raise ValueError("Conflicting event identity")
        current[event_id] = item
    if len(current) > 1:
        raise ValueError("Ambiguous current event")
    return next(iter(current.values()), None)


def schedule_snapshot(payload, now=None):
    """Keep only current musical metadata, clocks and optional artwork, never profiles."""
    from app.services.catalog import safe_url

    if not isinstance(payload, dict) or not isinstance(payload.get("radios"), dict):
        raise ValueError("Invalid Skyrock radios")
    if type(payload.get("ts")) is not int or len(payload["radios"]) > 100:
        raise ValueError("Invalid Skyrock snapshot")
    result, invalid = {}, set()
    for key, radio in payload["radios"].items():
        try:
            track = parse_schedule(payload, radio=key, now=now)
            if track is None:
                continue
            info = next(
                item["info"]
                for item in radio["schedule"]
                if item.get("type") == "record"
                and f"skyrock:{item['info']['uid']}" == track.source_event_id
            )
            cover = safe_url(
                info.get("cover_big_uri") or info.get("cover_uri") or info.get("cover_mid_uri")
            )
            result[key] = (replace(track, cover_url=cover), timestamp(info, "end"))
        except (ValueError, KeyError, TypeError, AttributeError, StopIteration):
            invalid.add(key)
    return payload["ts"], result, invalid


@dataclass
class SharedSchedule:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    expires: float = 0
    clock: int = 0
    tracks: dict = field(default_factory=dict)
    invalid: set = field(default_factory=set)
    http: list = field(default_factory=list)
    retry_seconds: float | None = None
    retry_until: float = 0
    failure: tuple | None = None


# Each HTTP client's lifetime owns its own cache/lock (including in tests/event loops).
_schedules = WeakKeyDictionary()


async def shared_track(client, key):
    """One bounded request per 15 seconds, shared by collection and artwork.

    Failed requests are shared too. Never fall back to opening all the audio streams.
    Cached entries are checked again against wall time before each use.
    """
    from app.services.collection_diagnostics import Trace, trace_context
    from app.services.metadata import read_bounded

    state = _schedules.setdefault(client, SharedSchedule())
    async with state.lock:
        if monotonic() >= state.expires:
            state.tracks, state.invalid, state.failure = {}, set(), None
            trace = Trace()
            token = trace_context.set(trace)
            try:
                async with asyncio.timeout(5):
                    content = await read_bounded(client, SUPER_ENDPOINT)
                state.clock, state.tracks, state.invalid = schedule_snapshot(json.loads(content))
            except httpx.HTTPStatusError as error:
                state.failure = ("http", error.response.status_code)
            except (httpx.RequestError, OSError):
                state.failure = ("network", None)
            except TimeoutError:
                state.failure = ("timeout", None)
            except (ValueError, KeyError, TypeError, AttributeError):
                state.failure = ("format", None)
            finally:
                state.http = trace.responses
                state.retry_seconds = trace.summary.get("retry_after_seconds")
                trace_context.reset(token)
            state.expires = monotonic() + max(15, state.retry_seconds or 0)
            state.retry_until = monotonic() + (state.retry_seconds or 0)

    remaining = max(0, ceil(state.retry_until - monotonic()))
    parent = trace_context.get()
    if parent:
        parent.responses.extend(state.http[: max(0, 6 - len(parent.responses))])
        if state.retry_seconds is not None:
            parent.summary["retry_after_seconds"] = remaining
    if state.failure:
        kind, status = state.failure
        if kind == "http":
            headers = {"Retry-After": str(remaining)} if state.retry_seconds is not None else {}
            response = httpx.Response(
                status, headers=headers, request=httpx.Request("GET", SUPER_ENDPOINT)
            )
            response.raise_for_status()
        if kind == "network":
            raise httpx.RequestError("Skyrock schedule unavailable")
        if kind == "timeout":
            raise TimeoutError("Skyrock schedule timed out")
        raise ValueError("Invalid Skyrock schedule")
    if key in state.invalid:
        raise ValueError("Invalid radio schedule")
    now = datetime.now(UTC).timestamp()
    item = state.tracks.get(key)
    if not item or not -5 <= now - state.clock <= 60:
        return None
    track, end = item
    return track if track.source_started_ms / 1000 <= now < end else None


def stream_key(stream):
    if not isinstance(stream, str):
        return None
    url = urlsplit(stream)
    if url.hostname != "icecast.skyrock.net" or not url.path.startswith("/s/"):
        return None
    name = url.path.rsplit("/", 1)[-1]
    for suffix in ("_aac_128k", "_mp3_128k"):
        if name.endswith(suffix):
            key = name.removesuffix(suffix)
            return (
                key.replace("_", "-")
                if key in {"urban_music", "hit_skyrock", "hit_us", "1er_rap", "rap_rnb"}
                else key
            )
    return None
