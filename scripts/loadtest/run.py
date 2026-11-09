"""Reproducible, bounded k6 campaign. Reports contain aggregates only."""

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROUTES = ("session", "exclusions", "now", "top", "explore", "catalog", "history")


def validate(config):
    url = urlsplit(config["base_url"])
    if url.scheme not in {"http", "https"} or not url.hostname:
        raise ValueError("base_url must be an HTTP origin")
    if url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
        raise ValueError("base_url must contain no credentials, query or path")
    if not 1 <= config["max_vus"] <= 500:
        raise ValueError("max_vus must be between 1 and 500")
    if sum(config["weights"].values()) != 100 or set(config["weights"]) != {
        "radio",
        "ranking",
        "search",
    }:
        raise ValueError("three session weights must sum to 100")
    for key in ("steady_seconds", "session_seconds", "request_timeout_seconds", "min_api_samples"):
        if not isinstance(config[key], int) or config[key] < 1:
            raise ValueError(f"invalid {key}")
    if not 0 <= config["max_error_rate"] < 1:
        raise ValueError("invalid error threshold")
    return config


def next_candidate(low, high, maximum):
    if high is None:
        return min(maximum, max(1, low * 2)) if low < maximum else None
    return (low + high) // 2 if high - low > 1 else None


def resources():
    # Strict allowlist: never hostname, environment, interfaces, mount points or addresses.
    lines = Path("/proc/stat").read_text().splitlines()
    cpu = {}
    for line in lines:
        key, *numbers = line.split()
        if key == "cpu" or (key.startswith("cpu") and key[3:].isdigit()):
            values = list(map(int, numbers[:8]))
            cpu[key] = (sum(values), values[3] + values[4])
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            mem[key] = int(value.split()[0])
    net = [0, 0]
    for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
        interface, values = line.split(":", 1)
        if interface.strip() != "lo":
            values = values.split()
            net[0] += int(values[0])
            net[1] += int(values[8])
    return {"cpu": cpu, "memory_kib": mem, "network_bytes": net, "at": time.monotonic()}


def delta(previous, current):
    busy = []
    for key, (total, idle) in current["cpu"].items():
        before_total, before_idle = previous["cpu"].get(key, (total, idle))
        elapsed = total - before_total
        if elapsed:
            busy.append((key, 100 * (1 - (idle - before_idle) / elapsed)))
    memory = current["memory_kib"]
    seconds = current["at"] - previous["at"]
    return {
        "cpu_percent": next((value for key, value in busy if key == "cpu"), 0),
        "busiest_core_percent": max((value for key, value in busy if key != "cpu"), default=0),
        "available_memory_percent": 100 * memory["MemAvailable"] / memory["MemTotal"],
        "swap_used_kib": memory["SwapTotal"] - memory["SwapFree"],
        "receive_bytes_per_second": max(
            0, current["network_bytes"][0] - previous["network_bytes"][0]
        )
        / seconds,
        "send_bytes_per_second": max(0, current["network_bytes"][1] - previous["network_bytes"][1])
        / seconds,
    }


def classify(metrics, config):
    samples = sum(metrics.get(f"api_{route}_count", {}).get("count", 0) for route in ROUTES)
    if samples < config["min_api_samples"]:
        return "inconclusive", ["insufficient_api_samples"]
    reasons = []
    for route in ROUTES:
        count = metrics.get(f"api_{route}_count", {}).get("count", 0)
        if (
            count
            and metrics.get(f"api_{route}_errors", {}).get("rate", 1) > config["max_error_rate"]
        ):
            reasons.append(f"{route}:errors")
        if count and metrics.get(f"api_{route}_ms", {}).get("p(90)", math.inf) > config["p90_ms"]:
            reasons.append(f"{route}:p90")
    if metrics.get("site_errors", {}).get("rate", 0) > config["max_error_rate"]:
        reasons.append("html_or_asset_errors")
    # Do not turn an absent/rare expensive route into a false proof of capacity.
    coverage = [r for r in ROUTES if metrics.get(f"api_{r}_count", {}).get("count", 0) < 5]
    if reasons:
        return "fail", reasons
    if coverage:
        return "inconclusive", ["insufficient_route_samples:" + ",".join(coverage)]
    return "pass", []


def run_plateau(k6, config_path, config, target, output):
    result_file = output / f"metrics-{target}-{time.time_ns()}.json"
    env = dict(
        os.environ, PROFILE=str(config_path), TARGET_VUS=str(target), RESULT_FILE=str(result_file)
    )
    command = [k6, "run", "--quiet", "--log-output", "none", str(HERE / "scenario.js")]
    started_at = time.time()
    before = resources()
    samples = []
    aborted = False
    excessive = 0
    with subprocess.Popen(
        command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ) as process:
        deadline = (
            time.monotonic()
            + config["ramp_seconds"]
            + config["warmup_seconds"]
            + config["steady_seconds"]
            + 45
        )
        while process.poll() is None:
            time.sleep(2)
            current = resources()
            sample = delta(before, current)
            before = current
            samples.append(sample)
            excessive = (
                excessive + 1
                if (
                    sample["cpu_percent"] > config["max_generator_cpu_percent"]
                    or sample["available_memory_percent"] < config["min_available_memory_percent"]
                )
                else 0
            )
            if excessive >= 5 or time.monotonic() > deadline:
                aborted = True
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                break
    summary = {
        "vus": target,
        "started_unix_seconds": started_at,
        "finished_unix_seconds": time.time(),
        "generator": {
            "samples": len(samples),
            "max_cpu_percent": max((s["cpu_percent"] for s in samples), default=0),
            "max_busiest_core_percent": max(
                (s["busiest_core_percent"] for s in samples), default=0
            ),
            "min_available_memory_percent": min(
                (s["available_memory_percent"] for s in samples), default=0
            ),
            "max_receive_bytes_per_second": max(
                (s["receive_bytes_per_second"] for s in samples), default=0
            ),
            "max_send_bytes_per_second": max(
                (s["send_bytes_per_second"] for s in samples), default=0
            ),
            "swap_growth_kib": max((s["swap_used_kib"] for s in samples), default=0)
            - min((s["swap_used_kib"] for s in samples), default=0),
        },
    }
    if aborted or process.returncode not in {0, 99} or not result_file.exists():
        return {**summary, "verdict": "inconclusive", "reasons": ["generator_abort_or_k6_failure"]}
    metrics = json.loads(result_file.read_text())["metrics"]
    verdict, reasons = classify(metrics, config)
    if process.returncode == 99:
        verdict, reasons = "fail", [*reasons, "safety_error_threshold"]
    if summary["generator"]["swap_growth_kib"] > 0:
        verdict, reasons = "inconclusive", ["generator_swap_growth"]
    return {**summary, "verdict": verdict, "reasons": reasons, "metrics": metrics}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "profile.json")
    parser.add_argument("--k6", default=shutil.which("k6"))
    parser.add_argument(
        "--run", action="store_true", help="Send real traffic; default only prints the plan"
    )
    parser.add_argument("--vus", type=int, help="Only one plateau, without capacity search")
    parser.add_argument("--output", type=Path, default=Path(".plan/k6-results"))
    args = parser.parse_args()
    config = validate(json.loads(args.config.read_text()))
    if args.vus is not None and not 1 <= args.vus <= config["max_vus"]:
        parser.error("--vus outside configured bound")
    print(
        json.dumps(
            {
                "max_vus": config["max_vus"],
                "weights": config["weights"],
                "p90_ms": config["p90_ms"],
                "steady_seconds": config["steady_seconds"],
                "traffic_enabled": args.run,
            }
        )
    )
    if not args.run:
        return
    if not args.k6:
        parser.error("k6 executable is required")
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "profile": config,
        "profile_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
        "scenario_sha256": hashlib.sha256((HERE / "scenario.js").read_bytes()).hexdigest(),
        "system": platform.system(),
        "architecture": platform.machine(),
        "logical_cpus": os.cpu_count(),
        "k6_version": subprocess.check_output([args.k6, "version"], text=True).strip(),
        "results": [],
    }
    low, high = 0, None
    target = args.vus or 1
    with tempfile.TemporaryDirectory(prefix="radio-k6-") as directory:
        frozen = Path(directory) / "profile.json"
        frozen.write_text(json.dumps(config))
        while target is not None:
            result = run_plateau(args.k6, frozen, config, target, args.output.resolve())
            manifest["results"].append(result)
            print(json.dumps({k: result[k] for k in ("vus", "verdict", "reasons")}), flush=True)
            if result["verdict"] == "pass":
                low = target
            elif result["verdict"] == "fail":
                high = target
            else:
                break
            (args.output / "campaign.json").write_text(json.dumps(manifest, indent=2))
            if args.vus:
                break
            target = next_candidate(low, high, config["max_vus"])
            if target is not None:
                time.sleep(config["cooldown_seconds"])
        repeated = False
        if not args.vus and target is None and high is not None:
            repeated = True
            for candidate in [n for n in (low, high) if n > 0]:
                expected = "pass" if candidate == low else "fail"
                for _ in range(config["repeat_boundary"]):
                    time.sleep(config["cooldown_seconds"])
                    result = run_plateau(args.k6, frozen, config, candidate, args.output.resolve())
                    manifest["results"].append(result)
                    print(
                        json.dumps({"repeat_vus": candidate, "verdict": result["verdict"]}),
                        flush=True,
                    )
                    if result["verdict"] != expected:
                        repeated = False
                        break
                if not repeated:
                    break
    manifest["bounds"] = {"last_pass": low, "first_fail": high, "boundary_repeated": repeated}
    manifest["limitations"] = [
        "HTTP simulation, not browser rendering",
        "No third-party audio or assets",
        "Local telemetry cannot prove network headroom or Windows-host headroom",
        "Server telemetry must be correlated separately",
        "Boundary needs repeated plateaus before claiming sustainable capacity",
    ]
    (args.output / "campaign.json").write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
