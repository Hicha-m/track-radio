"""Bounded cadence and HTTP backoff shared by worker and persisted diagnostics."""

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from app.config import settings


def retry_after(value, now=None):
    if not isinstance(value, str) or len(value) > 128:
        return None
    now = now or datetime.now(UTC)
    try:
        if value.isascii() and value.isdigit():
            seconds = int(value)
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            seconds = max(0, (date - now).total_seconds())
        return min(seconds, 2**31 - 1)
    except (ValueError, TypeError, OverflowError):
        return None


def failure_delay(failures, provider_delay=None):
    delay = min(300, settings.poll_interval_seconds * 2 ** min(failures, 4))
    return max(delay, provider_delay or 0)


def next_delay(schedule, now):
    # Refresh config/heartbeat at least once per base interval, even during a long cooldown.
    return max(
        1,
        min(
            settings.poll_interval_seconds,
            min(schedule.values(), default=now + settings.poll_interval_seconds) - now,
        ),
    )
