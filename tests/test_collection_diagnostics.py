"""Worker evidence, bounded admin reads, privacy, failures and on-worker probes."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import sessionmaker

from app.models import CollectionEvent, CollectionStatus, GlobalExclusion, Play, Station
from app.services import collection_diagnostics as diag
from app.services.collection_overview import CollectionFilters, overview
from app.services.exclusions import exclusion_id
from app.worker import collect_round, collect_station

AUTH = {"Authorization": "Bearer test-admin-token"}


def stream_response(status, *, json=None, text=None, content=None, headers=None):
    import json as codec

    body = (
        codec.dumps(json).encode()
        if json is not None
        else (text.encode() if text is not None else content or b"")
    )

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield body

    return httpx.Response(status, headers=headers, stream=Body())


@pytest.fixture
def setup_worker(db, monkeypatch):
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    monkeypatch.setattr("app.worker.SessionLocal", factory)
    station = Station(
        name="Test",
        slug="test",
        stream_url="https://radio.test/stream",
        source_type="json",
        metadata_url="https://radio.test/meta",
        json_title_path="title",
        json_artist_path="artist",
    )
    db.add(station)
    db.commit()
    return station, factory


def run_station(station, handler):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await collect_station(station, client)

    return asyncio.run(run())


def music(request):
    return stream_response(
        200,
        json={"artist": "PRIVATE ARTIST", "title": "PRIVATE TRACK"},
        headers={
            "Date": "Sun, 08 Nov 2026 10:00:00 GMT",
            "Age": "12",
            "Last-Modified": "Sun, 08 Nov 2026 09:59:59 GMT",
            "Cache-Control": "no-cache",
            "X-Secret": "NEVER SAVE",
        },
    )


@pytest.mark.parametrize(
    "failure,expected,http_status",
    [
        (403, "http_error", 403),
        (429, "http_error", 429),
        (503, "http_error", 503),
        ("timeout", "timeout", None),
        ("network", "network_error", None),
        ("bad_json", "format_error", 200),
        ("bad_path", "format_error", 200),
        ("empty", "empty", 200),
        ("empty_body", "empty", 200),
    ],
)
def test_failures_and_recovery_are_distinct_and_payload_free(
    db, client, setup_worker, failure, expected, http_status, caplog
):
    station, _ = setup_worker

    def fail(request):
        if isinstance(failure, int):
            return stream_response(failure, text="PRIVATE UPSTREAM PAYLOAD")
        if failure == "timeout":
            raise httpx.ReadTimeout("PRIVATE EXCEPTION")
        if failure == "network":
            raise httpx.ConnectError("PRIVATE EXCEPTION")
        if failure == "bad_json":
            return stream_response(200, text="PRIVATE UPSTREAM PAYLOAD")
        if failure == "empty_body":
            return stream_response(200, content=b"")
        return stream_response(
            200, json={} if failure == "bad_path" else {"title": "", "artist": None}
        )

    try:
        assert run_station(station, fail) is False
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    data = client.get("/api/admin/collection", headers=AUTH).json()
    item, attempt = data["items"][0], data["events"][0]
    assert item["health"] == "error"
    assert item["errors"] == item["consecutive_errors"] == 1
    assert attempt["outcome"] == expected and attempt["http_status"] == http_status
    assert attempt["duration_ms"] >= 0 and attempt["finished_at"]
    assert attempt["started_at"].endswith("+00:00")
    assert attempt["finished_at"].endswith("+00:00")
    assert item["last_attempt_at"].endswith("+00:00")
    assert attempt["next_attempt_at"]
    assert run_station(station, music) is True
    data = client.get("/api/admin/collection", headers=AUTH).json()
    item = data["items"][0]
    assert item["health"] == "observed" and item["last_success_at"]
    assert item["successes"] == 1 and item["consecutive_errors"] == 0 and item["errors"] == 1
    assert data["events"][0]["outcome"] == "new"
    assert data["events"][0]["details"]["http"][0]["age"] == 12
    assert "PRIVATE" not in json.dumps(data) and "NEVER SAVE" not in json.dumps(data)
    assert "PRIVATE" not in caplog.text
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Play)) == 1


def test_unchanged_excluded_and_old_events_do_not_look_like_network_failure(
    db, client, setup_worker
):
    station, factory = setup_worker
    run_station(station, music)
    run_station(station, music)
    with factory() as session:
        status = session.get(CollectionStatus, station.id)
        original_observation = status.last_observation_at
        status.last_change_at = datetime.now(UTC) - timedelta(hours=1)
        session.commit()
    data = client.get("/api/admin/collection", headers=AUTH).json()
    assert data["items"][0]["health"] == "unchanged"
    assert data["items"][0]["consecutive_errors"] == 0
    assert data["events"][0]["outcome"] == "unchanged"
    db.add(
        GlobalExclusion(
            id=exclusion_id("PRIVATE ARTIST", "PRIVATE TRACK"),
            artist="PRIVATE ARTIST",
            title="PRIVATE TRACK",
            artist_normalized="private artist",
            title_normalized="private track",
        )
    )
    db.commit()
    run_station(station, music)
    data = client.get(
        "/api/admin/collection",
        headers=AUTH,
        params={"exclusions": json.dumps([{"artist": "PRIVATE ARTIST", "title": "PRIVATE TRACK"}])},
    ).json()
    assert data["events"][0]["outcome"] == "excluded"
    assert data["items"][0]["successes"] == 3 and data["items"][0]["errors"] == 0
    assert "PRIVATE" not in json.dumps(data)
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Play)) == 1  # History retained.
    with factory() as session:
        status = session.get(CollectionStatus, station.id)
        status.last_observation_at = datetime.now(UTC) - timedelta(minutes=10)
        session.commit()
    data = client.get("/api/admin/collection", headers=AUTH).json()
    assert data["items"][0]["health"] == "observation_old"
    assert original_observation is not None


def test_round_correlation_concurrency_and_backoff_do_not_mix_station_evidence(
    tmp_path, monkeypatch
):
    from app.database import make_engine
    from app.models import Base

    engine = make_engine(f"sqlite:///{tmp_path / 'concurrent.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("app.worker.SessionLocal", factory)
    station = Station(
        name="Test",
        slug="test",
        source_type="json",
        stream_url="https://radio.test/stream",
        metadata_url="https://radio.test/meta",
        json_title_path="title",
    )
    other = Station(
        name="Other",
        slug="other",
        source_type="json",
        stream_url="https://other.test/stream",
        metadata_url="https://other.test/meta",
        json_title_path="title",
    )
    with factory() as db:
        db.add_all([station, other])
        db.commit()

    async def handler(request):
        await asyncio.sleep(0.001)
        return (
            stream_response(403)
            if request.url.host == "radio.test"
            else stream_response(200, json={"title": "safe"})
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            retries = {}
            round_id = await collect_round(client, retries)
            assert station.id in retries and other.id not in retries
            await collect_round(client, retries)
            return round_id

    round_id = asyncio.run(run())
    with factory() as session:
        attempts = list(session.scalars(select(CollectionEvent).order_by(CollectionEvent.id)))
        first = [row for row in attempts if row.round_id == round_id]
        assert len(first) == 2
        assert {row.station_id: row.http_status for row in first} == {
            station.id: 403,
            other.id: 200,
        }
        assert session.get(CollectionStatus, station.id).attempts == 1
        assert session.get(CollectionStatus, other.id).attempts == 2
    engine.dispose()


def test_attempt_is_visible_before_network_and_abandoned_attempt_is_recognized(db, setup_worker):
    station, factory = setup_worker
    old_id = diag.begin(factory, station.id, station.source_type)

    def handler(request):
        with factory() as session:
            rows = list(session.scalars(select(CollectionEvent).order_by(CollectionEvent.id)))
            assert rows[0].outcome == "interrupted"
            assert rows[-1].outcome == "running" and rows[-1].finished_at is None
        return music(request)

    run_station(station, handler)
    with factory() as session:
        assert session.get(CollectionEvent, old_id).outcome == "interrupted"
        assert session.get(CollectionStatus, station.id).consecutive_errors == 0


def test_admin_auth_filters_pagination_privacy_and_read_cost(db, client, setup_worker):
    station, _ = setup_worker
    now = datetime.now(UTC)
    for i in range(6):
        db.add(
            CollectionEvent(
                attempt_id=uuid4().hex,
                round_id=uuid4().hex,
                station_id=station.id,
                kind="collection",
                source="icy",
                started_at=now,
                outcome="http_error" if i % 2 else "new",
                details={
                    "raw": "SECRET",
                    "artist": "HIDDEN",
                    "http": [{"status": 403, "secret": "TOKEN"}],
                },
            )
        )
    db.commit()
    assert client.get("/api/admin/collection").status_code == 401
    assert (
        client.post("/api/admin/collection/probes", json={"station_id": station.id}).status_code
        == 401
    )
    first = client.get(
        "/api/admin/collection", headers=AUTH, params={"outcome": "http_error", "limit": 2}
    )
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    data = first.json()
    assert len(data["events"]) == 2 and data["next_before_id"]
    next_data = client.get(
        "/api/admin/collection",
        headers=AUTH,
        params={"outcome": "http_error", "limit": 2, "before_id": data["next_before_id"]},
    ).json()
    assert len(next_data["events"]) == 1
    assert not set(e["id"] for e in data["events"]) & set(e["id"] for e in next_data["events"])
    assert not any(secret in first.text for secret in ("SECRET", "HIDDEN", "TOKEN"))
    assert client.get("/api/admin/collection?outcome=arbitrary", headers=AUTH).status_code == 422
    assert client.get("/api/admin/collection?hours=1000", headers=AUTH).status_code == 422
    assert client.get("/api/admin/collection?limit=101", headers=AUTH).status_code == 422
    assert client.get("/api/admin/collection?hours=6", headers=AUTH).status_code == 200
    statements = []

    def count(*args):
        statements.append(args[2])

    event.listen(db.get_bind(), "before_cursor_execute", count)
    try:
        overview(db, CollectionFilters())
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", count)
    assert len(statements) == 2


def test_retention_caps_do_not_delete_any_plays(db, setup_worker, monkeypatch):
    station, factory = setup_worker
    run_station(station, music)
    now = datetime.now(UTC)
    # Small caps exercise the same indexed deletion against real SQL.
    monkeypatch.setattr(diag, "STATION_EVENT_LIMIT", 3)
    monkeypatch.setattr(diag, "GLOBAL_EVENT_LIMIT", 4)
    other = Station(name="Other", slug="other", stream_url="https://other.test")
    db.add(other)
    db.commit()
    for station_id in (station.id, other.id):
        for i in range(6):
            db.add(
                CollectionEvent(
                    attempt_id=uuid4().hex,
                    station_id=station_id,
                    source="icy",
                    started_at=now - timedelta(hours=50) if i == 0 else now,
                    kind="collection",
                    outcome="unchanged",
                )
            )
    db.commit()
    diag.prune(factory)
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(CollectionEvent)) == 4
    assert (
        db.scalar(
            select(func.count())
            .select_from(CollectionEvent)
            .where(CollectionEvent.station_id == other.id)
        )
        == 3
    )
    assert db.scalar(select(func.count()).select_from(Play)) == 1


def test_worker_probes_are_fixed_bounded_rate_limited_and_never_record_music(
    db, client, setup_worker
):
    station, factory = setup_worker
    station.name = "Skyrock"
    station.slug = "skyrock"
    station.stream_url = "https://icecast.skyrock.net/s/natio_mp3_128k"
    station.source_type = "icy"
    db.commit()
    assert (
        client.post(
            "/api/admin/collection/probes",
            headers=AUTH,
            json={"station_id": station.id, "url": "https://evil.test"},
        ).status_code
        == 422
    )
    result = client.post(
        "/api/admin/collection/probes", headers=AUTH, json={"station_id": station.id}
    )
    assert result.status_code == 202 and result.json() == {"queued": 3}
    assert (
        client.post(
            "/api/admin/collection/probes", headers=AUTH, json={"station_id": station.id}
        ).status_code
        == 429
    )
    requests = []

    def handler(request):
        requests.append(str(request.url))
        if request.url.host == "icecast.skyrock.net":
            title = b"StreamTitle='PRIVATE ARTIST - PRIVATE TRACK';"
            block = title.ljust(((len(title) + 15) // 16) * 16, b"\x00")
            return stream_response(
                200,
                headers={"icy-metaint": "1024"},
                content=b"a" * 1024 + bytes([len(block) // 16]) + block,
            )
        payload = {
            "ts": 1794133935,
            "schedule": [
                {
                    "info": {"start_ts": "1794133916", "end_ts": "1794134068"},
                    "likes": [{"user_id": "PRIVATE"}],
                }
            ],
        }
        if request.url.path.endswith("/super"):
            payload = {"ts": payload["ts"], "radios": {"natio": {"schedule": payload["schedule"]}}}
        return stream_response(200, json=payload)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await diag.process_probe(factory, http, "a" * 32)
            assert len(requests) == 1  # Only one probe per round.
            await diag.process_probe(factory, http, "b" * 32)
            await diag.process_probe(factory, http, "c" * 32)

    asyncio.run(run())
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Play)) == 0
    data = client.get("/api/admin/collection?kind=probe", headers=AUTH).json()
    assert len(data["events"]) == 3 and all(e["outcome"] == "probe_ok" for e in data["events"])
    assert {e["round_id"] for e in data["events"]} == {"a" * 32, "b" * 32, "c" * 32}
    assert data["events"][0]["details"]["active_entries"] == 1
    assert "PRIVATE" not in json.dumps(data)
    assert data["items"][0]["attempts"] == 0  # Probes do not change collection health.


def test_probe_errors_and_cooldown_survive_sessions_without_affecting_collection(
    db, client, setup_worker
):
    station, factory = setup_worker
    station.slug = "skyrock"
    station.stream_url = "https://icecast.skyrock.net/s/natio_mp3_128k"
    db.commit()
    assert (
        client.post(
            "/api/admin/collection/probes", headers=AUTH, json={"station_id": station.id}
        ).status_code
        == 202
    )
    db.expire_all()
    db.get(CollectionStatus, station.id).probe_requested_at = datetime.now(UTC) - timedelta(
        minutes=6
    )
    db.commit()
    # Time alone must not allow a second batch while the worker has not run.
    assert (
        client.post(
            "/api/admin/collection/probes", headers=AUTH, json={"station_id": station.id}
        ).status_code
        == 429
    )

    def handler(request):
        if request.url.host == "icecast.skyrock.net":
            raise httpx.ReadTimeout("NEVER SAVE")
        if request.url.path.endswith("/super"):
            return stream_response(403, text="NEVER SAVE")
        return stream_response(200, content=b"")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            for _ in range(3):
                await diag.process_probe(factory, http, "a" * 32)

    asyncio.run(run())
    data = client.get("/api/admin/collection?kind=probe", headers=AUTH).json()
    assert {e["outcome"] for e in data["events"]} == {"timeout", "http_error", "empty"}
    assert data["items"][0]["errors"] == 0 and data["items"][0]["health"] == "unknown"
    assert "NEVER SAVE" not in json.dumps(data)
    assert (
        client.post(
            "/api/admin/collection/probes", headers=AUTH, json={"station_id": station.id}
        ).status_code
        == 202
    )


def test_diagnostics_migration_and_backup_preserve_history(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    from app.config import settings
    from app.database import make_engine
    from app.models import Base
    from scripts import transfer

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'migrations.db'}")
    config = Config("alembic.ini")
    command.upgrade(config, "0013")
    engine = make_engine(settings.database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        station = Station(name="Fixture", slug="fixture", stream_url="https://example.test/stream")
        db.add(station)
        db.flush()
        db.add(
            Play(
                station_id=station.id,
                artist="Fixture",
                title="Preserved",
                source="icy",
                raw_metadata="historical fixture",
                detected_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
            )
        )
        db.commit()
    command.upgrade(config, "head")
    event_id = diag.begin(factory, station.id, "icy")
    diag.finish(factory, diag.Trace(event_id=event_id), "unchanged")
    monkeypatch.setattr(transfer, "engine", engine)
    archive = tmp_path / "backup.gz"
    transfer.export_data(archive)
    restored = make_engine(f"sqlite:///{tmp_path / 'restored.db'}")
    Base.metadata.create_all(restored)
    monkeypatch.setattr(transfer, "engine", restored)
    transfer.import_data(archive)
    with sessionmaker(bind=restored)() as db:
        assert db.scalar(select(Play.raw_metadata)) == "historical fixture"
        assert db.scalar(select(CollectionStatus.successes)) == 1
        assert db.scalar(select(CollectionEvent.outcome)) == "unchanged"
    command.downgrade(config, "0013")
    assert "collection_events" not in inspect(engine).get_table_names()
    with factory() as db:
        assert db.scalar(select(Play.raw_metadata)) == "historical fixture"
    command.upgrade(config, "head")
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Play)) == 1
        assert db.scalar(select(func.count()).select_from(CollectionEvent)) == 0
    restored.dispose()
    engine.dispose()


def test_deployment_requires_current_worker_evidence_but_not_upstream_success(db, setup_worker):
    from scripts.check_collection_diagnostics import state

    station, factory = setup_worker
    assert state(db, "development") == "waiting"
    token = diag.round_context.set("a" * 32)
    try:
        event_id = diag.begin(factory, station.id, "json")
    finally:
        diag.round_context.reset(token)
    diag.finish(factory, diag.Trace(event_id=event_id), "timeout")
    db.expire_all()
    assert state(db, "development") == "ready"
    assert state(db, "a" * 40) == "waiting"
    station.enabled = False
    db.commit()
    assert state(db, "development") == "disabled"


def test_probe_retry_after_defers_normal_collection_and_remaining_probes(db, client, setup_worker):
    from app.worker import restore_schedule

    station, factory = setup_worker
    station.slug = "skyrock"
    station.stream_url = "https://icecast.skyrock.net/s/natio_mp3_128k"
    db.commit()
    assert (
        client.post(
            "/api/admin/collection/probes", headers=AUTH, json={"station_id": station.id}
        ).status_code
        == 202
    )
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return stream_response(503, headers={"Retry-After": "600"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await diag.process_probe(factory, http, "a" * 32)
            await diag.process_probe(factory, http, "b" * 32)

    asyncio.run(run())
    assert len(calls) == 1
    _, schedule = restore_schedule()
    import time

    assert schedule[station.id] - time.monotonic() > 590
    db.expire_all()
    assert db.get(CollectionStatus, station.id).errors == 0
