from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check():
    """Liveness — the process is up."""
    return {"status": "ok"}


@router.get("/readiness")
def readiness_check(db: Session = Depends(get_db)):
    """Readiness — the app.db connection actually works."""
    db.execute(text("SELECT 1"))
    return {"status": "ready"}