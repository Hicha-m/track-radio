import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.services.search_text import register_search

os.environ["DATABASE_URL"] = "sqlite:///./test-unused.db"
os.environ["ADMIN_TOKEN"] = "test-admin-token"


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(engine, "connect", register_search)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session
    engine.dispose()


@pytest.fixture
def client(db, monkeypatch):
    from app.database import get_db
    from app.main import app
    from app.services.explorer import _cache as explorer_cache
    from app.services.stats import _cache

    _cache.clear()
    explorer_cache.clear()

    def request_session():
        try:
            yield db
        finally:
            db.rollback()

    app.dependency_overrides[get_db] = request_session
    monkeypatch.setattr("app.main.resolve_public", lambda url: "93.184.216.34")
    with TestClient(app) as result:
        yield result
    app.dependency_overrides.clear()
