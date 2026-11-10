"""Small subprocess entry point: public metadata only, never media downloads."""

import json
import logging
import re
import sys


def youtube_rows(query):
    from yt_dlp import YoutubeDL

    class Quiet:
        def debug(self, *args):
            pass

        warning = debug
        error = debug

    with YoutubeDL(
        {
            "quiet": True,
            "no_warnings": True,
            "logger": Quiet(),
            "extract_flat": True,
            "skip_download": True,
            "cachedir": False,
            "socket_timeout": 8,
            "retries": 0,
            "extractor_retries": 0,
            "playlistend": 3,
        }
    ) as tool:
        result = tool.extract_info("ytsearch3:" + query, download=False)
    rows = []
    for item in (result or {}).get("entries", [])[:3]:
        if not isinstance(item, dict) or not re.fullmatch(
            r"[A-Za-z0-9_-]{11}", str(item.get("id", ""))
        ):
            continue
        if item.get("is_live") or item.get("live_status") not in {None, "not_live"}:
            continue
        rows.append(
            {
                "id": item["id"],
                "title": str(item.get("title") or "")[:500],
                "channel": str(item.get("channel") or "")[:300],
                "duration": item.get("duration"),
            }
        )
    return rows


def web_rows(query):
    from ddgs import DDGS
    from ddgs.engines import ENGINES
    from ddgs.exceptions import DDGSException

    DDGS.threads = 1
    # Bing is disabled by this pinned release. Never silently fall back to "auto".
    backends = [name for name in ("yahoo", "duckduckgo") if name in ENGINES["text"]]
    if not backends:
        raise ValueError("No configured search engine available")
    try:
        with DDGS(timeout=8) as tool:
            results = tool.text(query, backend=",".join(backends), max_results=3)
    except DDGSException as error:
        if str(error) == "No results found.":
            return []
        raise
    return [
        {"url": str(row.get("href") or "")[:2000]} for row in results[:3] if isinstance(row, dict)
    ]


def main():
    logging.disable(logging.CRITICAL)
    try:
        request = json.loads(sys.stdin.buffer.read(32768))
        query = request["query"]
        if not isinstance(query, str) or not 1 <= len(query) <= 1200:
            raise ValueError
        if request["kind"] == "youtube":
            rows = youtube_rows(query)
        elif request["kind"] == "web":
            rows = web_rows(query)
        else:
            raise ValueError
        response = {"status": "ok", "items": rows}
    except Exception:
        response = {"status": "error", "items": []}
    print(json.dumps(response, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
