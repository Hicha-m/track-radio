"""Discover artwork from the station's configured official site, with bounded caching."""

import asyncio
from collections import OrderedDict
from time import monotonic
from typing import Annotated
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Station
from app.services.catalog import safe_url
from app.services.network import metadata_client, validate_url

router = APIRouter(prefix="/api/stations")
_cache = OrderedDict()
_lock = asyncio.Lock()


def image_from_html(html, base):
    soup = BeautifulSoup(html, "html.parser")
    for selector, attribute in (
        ('meta[property="og:image"]', "content"),
        ('meta[name="twitter:image"]', "content"),
        ('link[rel="apple-touch-icon"]', "href"),
        ('link[rel="icon"]', "href"),
    ):
        node = soup.select_one(selector)
        if node and node.get(attribute):
            value = safe_url(urljoin(base, node[attribute]))
            if value:
                try:
                    validate_url(value)
                    return value
                except ValueError:
                    continue
    return None


async def discover_image(client, site):
    async with asyncio.timeout(12):
        async with client.stream("GET", site) as response:
            response.raise_for_status()
            if "html" not in response.headers.get("content-type", ""):
                return None
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk[: 256 * 1024 - len(body)])
                # Head metadata comes first; never download an unbounded page.
                if len(body) >= 256 * 1024:
                    break
            return image_from_html(bytes(body[: 256 * 1024]), str(response.url))


@router.get("/{station_id}/artwork")
async def station_artwork(station_id: int, db: Annotated[Session, Depends(get_db)]):
    station = db.get(Station, station_id)
    if station is None:
        raise HTTPException(404, "Radio inconnue.")
    site = station.website_url or station.metadata_url
    db.commit()
    if not site:
        raise HTTPException(404, "Site officiel non configuré.")
    async with _lock:
        cached = _cache.get(site)
        if cached and cached[0] > monotonic():
            image = cached[1]
        else:
            try:
                async with metadata_client() as client:
                    image = await discover_image(client, site)
            except (httpx.HTTPError, ValueError, TimeoutError):
                image = None
            _cache[site] = (monotonic() + (86400 if image else 900), image)
            _cache.move_to_end(site)
            while len(_cache) > 128:
                _cache.popitem(last=False)
    if not image:
        raise HTTPException(404, "Image officielle indisponible.")
    return RedirectResponse(image, headers={"Cache-Control": "public, max-age=3600"})
