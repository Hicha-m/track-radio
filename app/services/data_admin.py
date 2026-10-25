"""Explicit, expiring previews for administrative corrections of broadcasts."""

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.config import settings
from app.models import Play, Station
from app.services.catalog import ensure_entry
from app.services.track_identity import identity


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def play_data(play):
    return {
        "id": play.id,
        "station_id": play.station_id,
        "artist": play.artist,
        "title": play.title,
        "track_id": play.track_id,
        "detected_at": utc(play.detected_at).isoformat(),
        "last_seen_at": utc(play.last_seen_at).isoformat(),
        "source": play.source,
        "raw_metadata": play.raw_metadata,
    }


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    action: Literal["correct", "delete", "merge"]
    ids: list[int] = Field(min_length=1, max_length=100)
    keep_id: int | None = None
    artist: str | None = Field(None, max_length=300)
    title: str | None = Field(None, min_length=1, max_length=500)
    detected_at: datetime | None = None

    @field_validator("ids")
    @classmethod
    def valid_ids(cls, values):
        if any(v <= 0 for v in values) or len(set(values)) != len(values):
            raise ValueError("Identifiants positifs et distincts requis.")
        return sorted(values)

    @field_validator("detected_at")
    @classmethod
    def zoned_date(cls, value):
        if value is not None:
            if value.tzinfo is None or value > datetime.now(UTC):
                raise ValueError("Date passée avec fuseau horaire requise.")
            return utc(value)
        return value


def prepare(db, command):
    # Same lock order as the collector on PostgreSQL; no network while locked.
    station_ids = db.scalars(select(Play.station_id).where(Play.id.in_(command.ids))).all()
    db.execute(
        select(Station).where(Station.id.in_(station_ids)).order_by(Station.id).with_for_update()
    ).all()
    rows = db.scalars(
        select(Play).where(Play.id.in_(command.ids)).order_by(Play.id).with_for_update()
    ).all()
    if len(rows) != len(command.ids):
        raise HTTPException(409, "Une diffusion a disparu. Actualisez la sélection.")
    before = [play_data(row) for row in rows]
    after = []
    if command.action == "correct":
        if len(rows) != 1 or not command.title:
            raise HTTPException(422, "Choisissez une diffusion et renseignez son titre.")
        result = dict(before[0], artist=command.artist or None, title=command.title)
        result["track_id"] = identity(result["artist"], result["title"])
        if command.detected_at:
            result["detected_at"] = command.detected_at.isoformat()
            result["last_seen_at"] = max(result["last_seen_at"], result["detected_at"])
        after = [result]
    elif command.action == "merge":
        if len(rows) < 2 or command.keep_id not in command.ids:
            raise HTTPException(422, "Choisissez au moins deux diffusions et celle à conserver.")
        if len({(r.station_id, identity(r.artist, r.title)) for r in rows}) != 1:
            raise HTTPException(422, "Le regroupement exige la même radio et le même titre.")
        kept = next(r for r in before if r["id"] == command.keep_id)
        after = [dict(kept, last_seen_at=max(r["last_seen_at"] for r in before))]
    return rows, before, after


def signature(data):
    return hmac.new(
        settings.admin_token.get_secret_value().encode(), data.encode(), hashlib.sha256
    ).hexdigest()


def fingerprint(before):
    return hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()


def preview(db, command):
    _, before, after = prepare(db, command)
    data = json.dumps(
        {
            "command": command.model_dump(mode="json"),
            "fingerprint": fingerprint(before),
            "expires": time.time() + 600,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return {"before": before, "after": after, "token": data + "." + signature(data)}


def apply(db, token):
    try:
        data, mac = token.rsplit(".", 1)
        if not hmac.compare_digest(signature(data), mac):
            raise ValueError
        payload = json.loads(data)
        if payload["expires"] < time.time():
            raise ValueError
        command = Correction.model_validate(payload["command"])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(409, "Prévisualisation expirée ou invalide. Recommencez.") from None
    rows, before, after = prepare(db, command)
    if fingerprint(before) != payload["fingerprint"]:
        raise HTTPException(409, "Les données ont changé. Prévisualisez de nouveau.")
    for row in rows:
        result = next((r for r in after if r["id"] == row.id), None)
        if result is None:
            db.delete(row)
        else:
            for field in ("artist", "title", "track_id", "detected_at", "last_seen_at"):
                value = result[field]
                setattr(
                    row, field, datetime.fromisoformat(value) if field.endswith("_at") else value
                )
            ensure_entry(db, row.artist, row.title)
    db.commit()
    from app.metrics_api import invalidate_metrics
    from app.services.explorer import invalidate_explorer
    from app.services.stats import _cache, _lock

    invalidate_metrics()
    invalidate_explorer()
    with _lock:
        _cache.clear()
    return {"affected": len(before), "remaining": len(after)}
