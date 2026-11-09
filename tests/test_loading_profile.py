"""The production profiler must not mutate data or emit musical content."""

import json

from sqlalchemy import event

from app.models import Station
from scripts import profile_loading


def test_profile_is_read_only_and_only_prints_technical_evidence(db, monkeypatch, capsys):
    db.add(Station(name="PRIVATE_STATION", slug="private", stream_url="https://example.test"))
    db.commit()
    monkeypatch.setattr(profile_loading, "engine", db.get_bind())

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            return b"PRIVATE_PAYLOAD"

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr(profile_loading.urllib.request, "build_opener", lambda *args: Opener())
    statements = []

    def record(conn, cursor, statement, *args):
        statements.append(statement.lstrip().upper())

    event.listen(db.get_bind(), "before_cursor_execute", record)
    try:
        profile_loading.profile()
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", record)
    output = capsys.readouterr().out
    rows = [json.loads(line) for line in output.splitlines()]
    assert len(rows) == 17
    assert not any(row.get("error") for row in rows)
    assert "PRIVATE" not in output
    assert any(row.get("plans") for row in rows)
    assert not any(s.startswith(("INSERT", "UPDATE", "DELETE")) for s in statements)
    assert rows[1]["statements"] < rows[0]["statements"]
