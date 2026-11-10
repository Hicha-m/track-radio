"""Bounded public catalogue lookup; suggestions never constitute a recording choice."""

import asyncio
from time import monotonic
from uuid import UUID

import httpx
from fastapi import HTTPException

from app.services.catalog import exact_match, fetch_json, folded, safe_url
from app.services.youtube import automatic_choice, verify_link, video_id_from_url

AGENT = "TrackRadio/0.3 (https://github.com/Hicha-m/track-radio)"


def mbid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def credited(row):
    return "".join(
        credit.get("name", credit.get("artist", {}).get("name", "")) + credit.get("joinphrase", "")
        for credit in row.get("artist-credit", [])
        if isinstance(credit, dict)
    )


def quote(value):
    # Keep the Lucene query literal, including titles containing punctuation.
    return '"' + str(value or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def video_links(row):
    links = set()
    for relation in row.get("relations", []):
        if (
            relation.get("type-id") != "7e41ef12-a124-4324-afdb-fdbae687a89c"
            and relation.get("type") != "free streaming"
        ):
            continue
        try:
            links.add(video_id_from_url(relation.get("url", {}).get("resource", "")))
        except ValueError:
            continue
    return links


class Lookup:
    def __init__(self, client):
        self.client = client
        self.next_request = 0

    async def musicbrainz(self, path, params):
        await asyncio.sleep(max(0, self.next_request - monotonic()))
        self.next_request = monotonic() + 1.1
        return await fetch_json(
            self.client,
            "https://musicbrainz.org/ws/2/" + path,
            {**params, "fmt": "json"},
            headers={"User-Agent": AGENT},
        )

    async def find(self, artist, title, selected, *, want_cover, want_video):
        if not artist:
            return {"status": "not_found", "recordings": [], "video": None, "cover": None}
        query = f"recording:{quote(title)} AND artist:{quote(artist)}"
        isrc = selected.get("isrc")
        if isrc:
            query += f" AND isrc:{quote(isrc)}"
        data = await self.musicbrainz("recording/", {"query": query, "limit": 10})
        recordings = []
        for row in data.get("recordings", []):
            if not isinstance(row, dict) or not mbid(row.get("id")):
                continue
            if row.get("disambiguation") or not exact_match(
                artist, title, {"artist": credited(row), "title": row.get("title", "")}
            ):
                continue
            duration = selected.get("duration")
            length = row.get("length")
            if duration and length and abs(float(duration) - float(length) / 1000) > 8:
                continue
            recordings.append(row)
        if not recordings or len(recordings) > 3 or data.get("count", 0) > 10:
            return {
                "status": "ambiguous" if recordings else "not_found",
                "recordings": [],
                "video": None,
                "cover": None,
            }
        links, releases, ids, music_videos = set(), {}, [], set()
        for recording in recordings:
            key = mbid(recording["id"])
            row = await self.musicbrainz(
                "recording/" + key,
                {"inc": "url-rels+recording-rels+releases+artist-credits+isrcs"},
            )
            # Recheck lookup identity, not just the search engine's score.
            if not exact_match(
                artist, title, {"artist": credited(row), "title": row.get("title", "")}
            ):
                continue
            if isrc and isrc.upper() not in [s.upper() for s in row.get("isrcs", [])]:
                continue
            ids.append(key)
            links.update(video_links(row))
            for relation in row.get("relations", []):
                if relation.get("type-id") == "ce3de655-7451-44d1-9224-87eb948c205d":
                    video_key = mbid(relation.get("recording", {}).get("id"))
                    if video_key:
                        music_videos.add(video_key)
            for release in row.get("releases", []):
                key = mbid(release.get("id"))
                album = selected.get("album")
                if (
                    key
                    and release.get("status") == "Official"
                    and folded(album or title) == folded(release.get("title", ""))
                ):
                    releases[key] = release
        incomplete = len(music_videos) > 2
        for key in sorted(music_videos)[:2] if want_video else []:
            row = await self.musicbrainz("recording/" + key, {"inc": "url-rels+artist-credits"})
            if exact_match(
                artist, title, {"artist": credited(row), "title": row.get("title", "")}
            ) and not row.get("disambiguation"):
                links.update(video_links(row))
        candidates = []
        incomplete |= len(links) > 3
        for key in sorted(links)[:3] if want_video else []:
            try:
                candidate = await verify_link(artist, title, "https://youtu.be/" + key)
                candidates.append({k: candidate[k] for k in ("id", "title", "channel")})
            except HTTPException as error:
                if error.status_code != 422:
                    incomplete = True
        video = None if incomplete else automatic_choice(artist, title, candidates)
        if video:
            video = {**video, "source": "musicbrainz_oembed"}
        cover = None
        if want_cover:
            # Match the known album, or an official single bearing this exact title.
            for key in sorted(releases, key=lambda k: (releases[k].get("date", "9999"), k))[:2]:
                try:
                    artwork = await fetch_json(
                        self.client, "https://coverartarchive.org/release/" + key
                    )
                except httpx.HTTPStatusError as error:
                    if error.response.status_code == 404:
                        continue
                    raise
                images = [
                    i for i in artwork.get("images", []) if i.get("front") and i.get("approved")
                ]
                if len(images) == 1:
                    url = safe_url(images[0].get("thumbnails", {}).get("500"))
                    if url:
                        cover = {"url": url, "release_id": key, "source": "coverartarchive"}
                        break
        return {
            "status": "error"
            if incomplete
            else "matched"
            if video or cover
            else "ambiguous"
            if candidates
            else "not_found",
            "recordings": ids,
            "video": video,
            "cover": cover,
        }
