"""
Creates every table declared in db/models.py inside app.db.

Run once (and again any time a model changes) from inside backend/:

    python -m app.db.init_db
"""

from app.config import get_settings
from app.db.models import Base
from app.db.session import engine


def init_db() -> None:
    settings = get_settings()
    settings.ensure_storage_dirs()  # makes sure storage/sqlite/ exists before SQLite creates the file
    Base.metadata.create_all(bind=engine)
    print(f"Initialized {settings.resolved_app_db_path}")


if __name__ == "__main__":
    init_db()