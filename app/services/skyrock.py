"""Strict national schedule selection; never retain provider/user payloads."""

import json
from datetime import UTC, datetime
from urllib.parse import urlsplit

ENDPOINTS = {
    "https://skyrock.fm/api/v3/player/onair/super",
    "https://skyrock.fm/api/v3/player/onair",
}
NATIONAL_STREAM = "https://icecast.skyrock.net/s/natio_mp3_128k"


def timestamp(info, name):
    value = info[name + "_ts"]
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit():
        raise ValueError("Invalid event clock")
    epoch = int(value)
    date = datetime.fromisoformat(info[name])
    if date.tzinfo is None or date.timestamp() != epoch:
        raise ValueError("Inconsistent event clock")
    return epoch


def parse_schedule(payload, *, super_endpoint=True, now=None):
    from app.services.metadata import TrackMetadata, checked_metadata

    now = (now or datetime.now(UTC)).timestamp()
    if not isinstance(payload, dict) or type(payload.get("ts")) is not int:
        raise ValueError("Missing provider clock")
    clock = payload["ts"]
    if not -5 <= now - clock <= 60:
        return None
    schedule = (
        payload.get("radios", {}).get("natio", {}).get("schedule")
        if super_endpoint
        else payload.get("schedule")
    )
    if not isinstance(schedule, list) or len(schedule) > 100:
        raise ValueError("Invalid national schedule")
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


def stream_key(stream):
    if not isinstance(stream, str):
        return None
    url = urlsplit(stream)
    if url.hostname != "icecast.skyrock.net" or not url.path.startswith("/s/"):
        return None
    name = url.path.rsplit("/", 1)[-1]
    for suffix in ("_aac_128k", "_mp3_128k"):
        if name.endswith(suffix):
            return name.removesuffix(suffix)
    return None
