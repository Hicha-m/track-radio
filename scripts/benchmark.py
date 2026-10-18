"""Petit test HTTP en lecture seule ; résultats dépendants de la machine et de la base."""

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def benchmark(url, requests=200, concurrency=4):
    paths = ["/", "/api/plays?limit=25", "/api/stats/top?limit=10", "/?hours=0"]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def fetch(index):
        start = time.perf_counter()
        try:
            with opener.open(url.rstrip("/") + paths[index % len(paths)], timeout=30) as response:
                response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        except OSError:
            status = 0
        return status, 1000 * (time.perf_counter() - start)

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(fetch, range(requests)))
    elapsed = time.perf_counter() - start
    durations = sorted(duration for _, duration in results)
    return {
        "requests": requests,
        "concurrency": concurrency,
        "errors": sum(status != 200 for status, _ in results),
        "seconds": round(elapsed, 2),
        "requests_per_second": round(requests / elapsed, 2),
        "median_ms": round(statistics.median(durations), 2),
        "p95_ms": round(durations[min(len(durations) - 1, int(len(durations) * 0.95))], 2),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    if args.requests < 1 or not 1 <= args.concurrency <= 24:
        parser.error("requests >= 1 et concurrency entre 1 et 24 requis")
    result = benchmark(args.url, args.requests, args.concurrency)
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result["errors"]))
