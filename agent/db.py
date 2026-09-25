import os
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_engine = None
_SessionLocal: Optional[sessionmaker] = None


def get_engine():
    global _engine
    if _engine is None:
        database_url = os.environ["DATABASE_URL"]
        # Railway injects the postgres:// scheme. Name the driver explicitly
        # rather than relying on SQLAlchemy's default for postgresql:// —
        # that default switched from psycopg2 to psycopg (v3) in 2.1, which
        # took the whole site down with "No module named 'psycopg'" the first
        # time a rebuild picked 2.1 up. psycopg2-binary is what's installed.
        for scheme in ("postgres://", "postgresql://"):
            if database_url.startswith(scheme):
                database_url = "postgresql+psycopg2://" + database_url[len(scheme):]
                break
        _engine = create_engine(database_url, pool_pre_ping=True)
    return _engine


def get_sessionmaker() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def get_db():
    """FastAPI dependency: one session per request, closed when the request ends."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()
