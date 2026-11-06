"""Small, serial GET-only probes. Never retain response bodies or musical metadata."""

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta


def paths():
    since = (datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(days=7)).isoformat()
    return {
        "health": "/health",
        "home": "/",
        "recent": "/api/plays?limit=25",
        "chart_7d": "/api/stats/top?" + urllib.parse.urlencode({"limit": 20, "since": since}),
        "chart_all": "/api/stats/top?limit=20",
        "search_plays": "/api/explore?view=plays&page_size=25",
        "search_tracks": "/api/explore?view=tracks&page_size=25",
        "summary_24h": "/api/stats/summary",
    }


def request(base, path):
    started = time.perf_counter()
    status, size = 0, 0
    try:
        with urllib.request.urlopen(base.rstrip("/") + path, timeout=25) as response:
            status = response.status
            # Consume a bounded body without persisting or printing any values.
            size = len(response.read(1024 * 1024 + 1))
    except urllib.error.HTTPError as error:
        status = error.code
    except (OSError, TimeoutError):
        pass
    return {"status": status, "bytes": size, "seconds": round(time.perf_counter() - started, 4)}


def probe(base, samples=2):
    results = {}
    started = time.perf_counter()
    for label, path in paths().items():
        if time.perf_counter() - started > 110:
            results[label] = {"skipped": "overall time budget"}
            continue
        results[label] = [request(base, path) for _ in range(samples)]
        time.sleep(0.2)
    # A single expensive query alongside a health request identifies request blocking.
    with ThreadPoolExecutor(max_workers=2) as pool:
        heavy = pool.submit(request, base, "/api/stats/top?limit=19")
        time.sleep(0.1)
        health = request(base, "/health")
        results["overlap"] = {"chart": heavy.result(), "health": health}
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    args = parser.parse_args()
    print(json.dumps({"http": probe(args.base)}, indent=2))
