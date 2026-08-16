"""Engine and session factory.

Postgres-portable: nothing here or in models.py is SQLite-specific
beyond the connect_args guard below. Switching to Postgres is a
DASKE_DATABASE_URL change.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite(settings.database_url) else {},
)

if _is_sqlite(settings.database_url):
    # Enforce FKs in SQLite (Postgres does this natively).
    @event.listens_for(engine, "connect")
    def _fk_pragma(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
