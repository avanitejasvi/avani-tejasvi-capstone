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
        # Railway injects the postgres:// scheme; SQLAlchemy 2.x's psycopg2
        # dialect requires postgresql://.
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
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
