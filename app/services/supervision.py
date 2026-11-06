"""Persistent bounded operational state; never store upstream payloads or exceptions."""

from datetime import UTC, datetime

from sqlalchemy import delete, select

from app.models import WorkerEvent, WorkerState

CODES = {"started", "completed", "error", "waiting", "metadata", "enrichment", "youtube"}


def report(factory, name, code, *, total=None, completed=None, failed=None):
    if code not in CODES or name not in {"collector", "enrichment", "youtube"}:
        raise ValueError("Unknown operational event")
    now = datetime.now(UTC)
    with factory() as db:
        state = db.get(WorkerState, name)
        if state is None:
            state = WorkerState(
                name=name,
                state="running",
                started_at=now,
                heartbeat_at=now,
                total=0,
                completed=0,
                failed=0,
                activity=code,
            )
            db.add(state)
        state.heartbeat_at = now
        state.activity = code
        state.state = (
            "error"
            if code == "error"
            else "idle"
            if code in {"completed", "waiting"}
            else "running"
        )
        if code == "started":
            state.started_at = now
        for field, value in (("total", total), ("completed", completed), ("failed", failed)):
            if value is not None:
                setattr(state, field, value)
        if code in {"started", "completed", "error"}:
            db.add(WorkerEvent(worker=name, at=now, code=code))
            db.flush()
            cutoff = db.scalar(
                select(WorkerEvent.id).order_by(WorkerEvent.id.desc()).offset(199).limit(1)
            )
            if cutoff:
                db.execute(delete(WorkerEvent).where(WorkerEvent.id < cutoff))
        db.commit()
