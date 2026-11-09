"""Safety boundaries and capacity decisions for the load-test controller."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("loadtest_run", ROOT / "scripts/loadtest/run.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.fixture
def profile():
    return json.loads((ROOT / "scripts/loadtest/profile.json").read_text())


def test_doubling_never_exceeds_500():
    low = 0
    seen = []
    while (candidate := runner.next_candidate(low, None, 500)) is not None:
        seen.append(candidate)
        low = candidate
    assert seen == [1, 2, 4, 8, 16, 32, 64, 128, 256, 500]


def test_integer_bisection_terminates_at_adjacent_bounds():
    low, high = 256, 500
    while (candidate := runner.next_candidate(low, high, 500)) is not None:
        if candidate <= 371:
            low = candidate
        else:
            high = candidate
    assert (low, high) == (371, 372)
    assert runner.next_candidate(0, 1, 500) is None


@pytest.mark.parametrize(
    "url", ["https://user:secret@example.test", "https://example.test/?token=x", "file:///tmp/test"]
)
def test_credentials_and_non_origins_rejected(profile, url):
    profile["base_url"] = url
    with pytest.raises(ValueError):
        runner.validate(profile)


def metric_set(count=30, latency=999):
    metrics = {}
    for route in runner.ROUTES:
        metrics[f"api_{route}_count"] = {"count": count}
        metrics[f"api_{route}_ms"] = {"p(90)": latency}
        metrics[f"api_{route}_errors"] = {"rate": 0}
    return metrics


def test_slow_route_cannot_be_hidden_by_fast_global_percentile(profile):
    metrics = metric_set()
    metrics["api_explore_ms"]["p(90)"] = 1001
    assert runner.classify(metrics, profile) == ("fail", ["explore:p90"])
    metrics["api_explore_ms"]["p(90)"] = 1000
    assert runner.classify(metrics, profile)[0] == "pass"


def test_fast_errors_and_missing_routes_are_not_capacity_proofs(profile):
    metrics = metric_set()
    metrics["api_top_errors"]["rate"] = 0.5
    assert runner.classify(metrics, profile)[0] == "fail"
    del metrics["api_explore_count"]
    metrics["api_top_errors"]["rate"] = 0
    assert runner.classify(metrics, profile)[0] == "inconclusive"


def test_cap_cannot_be_disabled_by_profile(profile):
    profile["max_vus"] = 501
    with pytest.raises(ValueError):
        runner.validate(profile)
