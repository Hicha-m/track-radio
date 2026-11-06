"""Execute the actual deployment smoke-check with isolated service/database dependencies."""

from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import ServiceQuota
from app.services import youtube


def deployment_check():
    script = Path("scripts/deploy.sh").read_text()
    return script.split("compose exec -T api python - <<'PY'", 2)[2].split("\nPY\n", 1)[0]


def test_deployment_smoke_uses_persistent_database(db, monkeypatch):
    monkeypatch.setattr("app.database.SessionLocal", sessionmaker(bind=db.get_bind()))
    monkeypatch.setattr(settings, "youtube_api_key", SecretStr("test-only"))

    async def search(*args):
        return {
            "status": "matched",
            "candidates": [{"id": "abcdefghijk", "title": "Fixture", "channel": "Fixture"}],
        }

    monkeypatch.setattr(youtube, "search_remote", search)
    exec(compile(deployment_check(), "deploy-youtube-check", "exec"), {})
    assert db.get(ServiceQuota, "youtube").used == 1


def test_exhausted_quota_does_not_fail_an_otherwise_healthy_deployment(monkeypatch):
    async def exhausted(*args):
        raise HTTPException(429, "Quota atteint.")

    monkeypatch.setattr(youtube, "discover", exhausted)
    exec(compile(deployment_check(), "deploy-youtube-check", "exec"), {})


def test_real_youtube_failure_remains_visible_to_deployment(monkeypatch):
    async def failed(*args):
        raise HTTPException(503, "Service indisponible.")

    monkeypatch.setattr(youtube, "discover", failed)
    with pytest.raises(SystemExit) as error:
        exec(compile(deployment_check(), "deploy-youtube-check", "exec"), {})
    assert error.value.code == 1
