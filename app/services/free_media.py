"""Free, bounded discovery adapters; selected links remain provider-owned media."""

import asyncio
import html
import json
import os
import re
import sys
from time import monotonic
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException

from app.services.catalog import fetch_json, folded, retry_seconds
from app.services.youtube import automatic_choice, candidate_score, verify_link

STRATEGY = "free-media-v1"


def same_video_title(artist, title, caption):
    caption = folded(html.unescape(caption))
    wanted, singer = folded(title), folded(artist)
    if caption.startswith(singer + " "):
        caption = caption[len(singer) + 1 :]
    for suffix in (
        "official music video",
        "official lyric video",
        "official visualizer",
        "official video",
        "official audio",
        "clip officiel",
        "video officielle",
        "audio officiel",
        "lyric video",
        "music video",
        "visualizer",
        "lyrics",
        "paroles",
    ):
        if caption != wanted and caption.endswith(" " + suffix):
            caption = caption[: -len(suffix)].strip()
    return bool(wanted) and caption == wanted


class SearchUnavailable(ValueError):
    pass


async def run_tool(kind, query):
    # These libraries are synchronous. A killed subprocess also stops their threads.
    allowed = {
        "PATH",
        "PYTHONPATH",
        "SYSTEMROOT",
        "WINDIR",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "TMPDIR",
        "TEMP",
        "TMP",
    }
    environment = {k: v for k, v in os.environ.items() if k in allowed}
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.media_search_tools",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=environment,
    )
    try:
        async with asyncio.timeout(25):
            output, _ = await process.communicate(
                json.dumps({"kind": kind, "query": query}).encode()
            )
        if process.returncode or len(output) > 32768:
            raise SearchUnavailable
        result = json.loads(output)
        if (
            not isinstance(result, dict)
            or result.get("status") != "ok"
            or not isinstance(result.get("items"), list)
        ):
            raise SearchUnavailable
        return result["items"][:3]
    except (ValueError, TimeoutError, OSError) as error:
        raise SearchUnavailable("Recherche temporairement indisponible") from error
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def spotify_track(value):
    if not isinstance(value, str) or len(value) > 4096:
        return None
    try:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or url.hostname != "open.spotify.com"
            or url.port not in {None, 443}
            or url.username
            or url.password
        ):
            return None
        path = re.fullmatch(r"/(?:intl-[a-z]{2}/)?track/([A-Za-z0-9]{22})", url.path)
        return "https://open.spotify.com/track/" + path[1] if path else None
    except (ValueError, TypeError):
        return None


def spotify_image(value):
    if not isinstance(value, str) or len(value) > 4096:
        return None
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if url.scheme != "https" or url.port not in {None, 443} or url.username or url.password:
            return None
        if host != "i.scdn.co" and not re.fullmatch(r"image-cdn-[a-z0-9-]+\.spotifycdn\.com", host):
            return None
        return value if re.fullmatch(r"/image/[a-f0-9]{40,64}", url.path) else None
    except (ValueError, TypeError):
        return None


def artwork_id(value):
    key = urlsplit(value).path.rsplit("/", 1)[-1]
    # Album-art IDs encode the image size before the stable artwork identifier.
    return key[16:] if key.startswith("ab67616d") else key


async def spotify_cover(client, artist, title, selected, url):
    url = spotify_track(url)
    if not url:
        return None
    async with (
        asyncio.timeout(10),
        client.stream("GET", url, headers={"Accept-Language": "en"}) as response,
    ):
        response.raise_for_status()
        if spotify_track(str(response.url)) != url or "text/html" not in response.headers.get(
            "content-type", ""
        ):
            return None
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk[: 512 * 1024 - len(body)])
            # Spotify can stream Open Graph tags after </head>; keep a bounded page.
            if len(body) >= 512 * 1024:
                break
    soup = BeautifulSoup(bytes(body).decode("utf-8", errors="replace"), "html.parser")
    metadata = {m.get("property"): m.get("content", "") for m in soup.select("meta[property]")}
    found_title = metadata.get("og:title", "")
    parts = metadata.get("og:description", "").split(" · ")
    # Search snippets are not evidence. Require the canonical page's exact credits/version.
    if len(parts) < 3 or folded(artist) != folded(parts[0]) or folded(title) != folded(found_title):
        return None
    if selected.get("album") and folded(selected["album"]) != folded(parts[1]):
        return None
    image = spotify_image(metadata.get("og:image"))
    if not image:
        return None
    embed = await fetch_json(client, "https://open.spotify.com/oembed", {"url": url})
    thumbnail = spotify_image(embed.get("thumbnail_url"))
    if (
        folded(embed.get("title", "")) != folded(title)
        or not thumbnail
        or artwork_id(image) != artwork_id(thumbnail)
    ):
        return None
    return {"url": image, "source": "ddgs_spotify", "page": url, "album": parts[1]}


class Lookup:
    strategy = STRATEGY

    def __init__(self, client, fallback):
        self.client = client
        self.fallback = fallback
        self.pauses = {}

    async def youtube(self, artist, title):
        rows = await run_tool("youtube", f"{artist} {title}")
        plausible = []
        for row in rows:
            if not isinstance(row, dict) or not re.fullmatch(
                r"[A-Za-z0-9_-]{11}", str(row.get("id", ""))
            ):
                continue
            score = candidate_score(
                artist,
                title,
                {"title": row.get("title", ""), "channelTitle": row.get("channel", "")},
            )
            if (
                score is not None
                and score >= 4
                and same_video_title(artist, title, row.get("title", ""))
            ):
                plausible.append(row)
        verified = []
        for row in plausible:
            try:
                candidate = await verify_link(artist, title, "https://youtu.be/" + row["id"])
                if same_video_title(artist, title, candidate["title"]):
                    verified.append({k: candidate[k] for k in ("id", "title", "channel")})
            except HTTPException as error:
                if error.status_code != 422:
                    raise SearchUnavailable from error
        video = automatic_choice(artist, title, verified)
        return {
            "status": "matched" if video else "ambiguous" if verified else "not_found",
            "video": {**video, "source": "yt_dlp_oembed"} if video else None,
        }

    async def cover(self, artist, title, selected):
        rows = await run_tool("web", f"{artist} {title} site:open.spotify.com/track")
        seen = set()
        for row in rows:
            url = spotify_track(row.get("url")) if isinstance(row, dict) else None
            if not url or url in seen:
                continue
            seen.add(url)
            try:
                found = await spotify_cover(self.client, artist, title, selected, url)
            except httpx.HTTPStatusError as error:
                if error.response.status_code in {404, 410}:
                    continue
                raise
            if found:
                return {"status": "matched", "cover": found}
        return {"status": "not_found", "cover": None}

    async def find(self, artist, title, selected, *, want_cover, want_video):
        result = {"video": None, "cover": None, "recordings": [], "sources": {}}
        if not artist:
            return {**result, "status": "not_found"}
        for name, wanted in (("youtube", want_video), ("cover", want_cover)):
            if not wanted:
                continue
            if self.pauses.get(name, 0) > monotonic():
                result["sources"][name] = "error"
                continue
            try:
                async with asyncio.timeout(35):
                    data = await (
                        self.youtube(artist, title)
                        if name == "youtube"
                        else self.cover(artist, title, selected)
                    )
                result.update({k: data[k] for k in ("video", "cover") if k in data})
                result["sources"][name] = data["status"]
            except (
                httpx.HTTPError,
                HTTPException,
                SearchUnavailable,
                ValueError,
                TypeError,
                AttributeError,
                KeyError,
                TimeoutError,
            ) as error:
                delay = (
                    retry_seconds(error.response.headers.get("retry-after"))
                    if isinstance(error, httpx.HTTPStatusError)
                    else 900
                )
                self.pauses[name] = monotonic() + max(60, delay)
                result["sources"][name] = "error"
        missing_cover = want_cover and not result["cover"]
        missing_video = want_video and not result["video"]
        if missing_cover or missing_video:
            try:
                async with asyncio.timeout(35):
                    extra = await self.fallback.find(
                        artist, title, selected, want_cover=missing_cover, want_video=missing_video
                    )
                result["recordings"] = extra.get("recordings", [])
                for field in ("video", "cover"):
                    if not result[field] and extra.get(field):
                        result[field] = extra[field]
                result["sources"]["musicbrainz"] = extra["status"]
            except (
                httpx.HTTPError,
                HTTPException,
                ValueError,
                TypeError,
                AttributeError,
                KeyError,
                TimeoutError,
            ):
                result["sources"]["musicbrainz"] = "error"
        states = result["sources"].values()
        result["status"] = (
            "error"
            if "error" in states
            else "matched"
            if result["video"] or result["cover"]
            else "ambiguous"
            if "ambiguous" in states
            else "not_found"
        )
        return result
