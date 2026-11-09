"""A slow uncached ranking must not block a different cached ranking."""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from sqlalchemy import event

from app.services import stats
from app.services.catalog import attach_metadata, ensure_entry, public_metadata


def test_warm_ranking_does_not_wait_for_unrelated_sql(monkeypatch):
    started, release, warmed = Event(), Event(), Event()

    class Bind:
        class dialect:
            name = "sqlite"

    bind = Bind()

    class DB:
        def __init__(self, slow=False):
            self.slow = slow

        def get_bind(self):
            return bind

        def execute(self, query):
            if self.slow:
                started.set()
                assert release.wait(5)
            return [("Artist", "Title", 1)]

    monkeypatch.setattr(stats, "global_revision", lambda db: "same")
    monkeypatch.setattr(stats, "_cache", OrderedDict())
    expected = stats.cached_top(DB(), 1, None, None, 20)

    def warm():
        result = stats.cached_top(DB(), 1, None, None, 20)
        warmed.set()
        return result

    with ThreadPoolExecutor(2) as pool:
        cold = pool.submit(stats.cached_top, DB(True), 2, None, None, 20)
        try:
            assert started.wait(2)
            hit = pool.submit(warm)
            available = warmed.wait(2)
        finally:
            release.set()
        cold.result()
        assert hit.result() == expected
        assert available


def test_public_metadata_projection_preserves_response_without_internal_payloads(db):
    entry = ensure_entry(db, "Artist", "Song")
    entry.candidates = [{"private": "candidate-payload"}]
    entry.lyrics = {"private": "lyrics-payload"}
    entry.selected = {"provider": "itunes", "provider_id": "1", "cover_url": "https://a.test/a"}
    db.commit()
    expected = public_metadata(entry)
    statements = []

    def capture(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", capture)
    try:
        result = attach_metadata(db, [{"artist": "Artist", "title": "Song"}])
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture)
    assert result[0]["metadata"] == expected
    assert len(statements) == 1
    assert "track_metadata.candidates" not in statements[0]
    assert "track_metadata.lyrics," not in statements[0]
    assert "track_metadata.lyrics_candidates" not in statements[0]
    assert "track_metadata.attempts" not in statements[0]
