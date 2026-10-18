from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.services.search_text import register_search


def make_engine(url: str):
    options = {"pool_size": 2, "max_overflow": 0, "pool_timeout": 5}
    if url.startswith("sqlite:"):
        options["connect_args"] = {"check_same_thread": False, "timeout": 5}
    result = create_engine(url, pool_pre_ping=True, **options)
    if result.dialect.name == "sqlite":
        event.listen(result, "connect", register_search)

        @event.listens_for(result, "connect")
        def configure_sqlite(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA cache_size=-2048")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return result


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as session:
        yield session
