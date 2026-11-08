from datetime import UTC, datetime

from app.services.collection_schedule import failure_delay, next_delay, retry_after


def test_retry_after_delta_date_invalid_and_progressive_backoff():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert retry_after("120", now) == 120
    assert retry_after("Thu, 01 Jan 2026 00:03:00 GMT", now) == 180
    for value in (None, "bad", "-2", "1.5", "x" * 129):
        assert retry_after(value, now) is None
    assert retry_after("Wed, 31 Dec 2025 23:59:00 GMT", now) == 0
    assert [failure_delay(i) for i in range(1, 6)] == [40, 80, 160, 300, 300]
    assert failure_delay(1, 600) == 600
    assert next_delay({1: 3000}, 0) == 20
    assert next_delay({1: -1}, 0) == 1
