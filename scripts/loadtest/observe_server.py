"""Bounded, read-only Oracle telemetry. Output is an explicit aggregate allowlist."""

import argparse
import json
import platform
import subprocess
import time
from pathlib import Path


def docker(*arguments):
    result = subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, timeout=15, check=True
    )
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=1800)
    args = parser.parse_args()
    if not 10 <= args.seconds <= 3600:
        parser.error("observation must last between 10 and 3600 seconds")
    print(
        json.dumps({"architecture": platform.machine(), "duration_seconds": args.seconds}),
        flush=True,
    )
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        memory = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                memory[key] = int(value.split()[0])
        sample = {"unix_seconds": time.time(), "memory_kib": memory, "services": {}}
        for service in ("api", "collector", "cloudflared"):
            try:
                container = docker("compose", "-f", "compose.yaml", "ps", "-q", service)
                if not container:
                    sample["services"][service] = {"state": "absent"}
                    continue
                # Never inspect Config.Env, labels, command line, network or mounts.
                config = json.loads(
                    docker("inspect", "--format", "{{json .HostConfig}}", container)
                )
                state = json.loads(docker("inspect", "--format", "{{json .State}}", container))
                stats = json.loads(
                    docker("stats", "--no-stream", "--format", "{{json .}}", container)
                )
                sample["services"][service] = {
                    "cpu_limit_nanocpus": config.get("NanoCpus"),
                    "memory_limit_bytes": config.get("Memory"),
                    "running": state.get("Running"),
                    "oom_killed": state.get("OOMKilled"),
                    "health": state.get("Health", {}).get("Status"),
                    "cpu": stats.get("CPUPerc"),
                    "memory": stats.get("MemUsage"),
                    "memory_percent": stats.get("MemPerc"),
                    "network_io": stats.get("NetIO"),
                    "block_io": stats.get("BlockIO"),
                    "pids": stats.get("PIDs"),
                }
                if service == "collector":
                    code = (
                        "import os,time; "
                        "print(max(0,time.time()-os.stat('/app/data/collector.heartbeat').st_mtime))"
                    )
                    sample["services"][service]["heartbeat_age_seconds"] = float(
                        docker("exec", container, "python", "-c", code)
                    )
            except (subprocess.SubprocessError, ValueError, OSError):
                sample["services"][service] = {"telemetry_error": True}
        print(json.dumps(sample), flush=True)
        time.sleep(min(5, max(0, deadline - time.monotonic())))


if __name__ == "__main__":
    main()
