from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.sqlalchemy_database_url,
    # SQLite + FastAPI: requests can be served on different threads than the
    # one that created the connection, so the default same-thread check must
    # be disabled. Safe here because each request gets its own Session.
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    """SQLite ignores FOREIGN KEY / ON DELETE CASCADE unless this is set per-connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db_session() -> Session:
    """
    Yields a session and guarantees it's closed afterwards. FastAPI's
    `deps.py` (point 8) will wrap this in a `Depends()`; kept here too so
    scripts/tests can use it directly without touching the API layer.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()