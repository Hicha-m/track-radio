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
        headers = {}

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


def test_live_timing_is_admin_opt_in_and_does_not_expose_data(client, db):
    from app.loading_profile import instrument

    instrument(db.get_bind())
    route = "/api/stats/top"
    assert "server-timing" not in client.get(route).headers
    assert "server-timing" not in client.get(route, headers={"X-Loading-Profile": "1"}).headers
    headers = {"X-Loading-Profile": "1", "Authorization": "Bearer test-admin-token"}
    response = client.get(route, headers=headers)
    assert response.status_code == 200
    timing = response.headers["server-timing"]
    assert "sql_execute;dur=" in timing and "sql_count;desc=" in timing
    import re

    assert int(re.search(r'sql_count;desc="(\d+)"', timing)[1]) > 0
    assert "SELECT" not in timing and "test-admin-token" not in timing
    assert "server-timing" not in client.get(route).headers
