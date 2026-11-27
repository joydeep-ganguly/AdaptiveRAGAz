# backend/app/scaling/metrics_router.py

"""
GET /metrics for Prometheus. Mounted by main.py only when SCALING_ENABLED=true.

It needs no login, as scrapers do not log in. The numbers hold no user data,
but they do show how busy the system is, so keep /metrics off the public
internet (allow only the monitoring system to reach it).
"""

from fastapi import APIRouter, Depends, Response

from app.scaling.metrics import metrics_content_type, render_metrics
from app.scaling.redis_client import get_redis

router = APIRouter(tags=["metrics"])


def get_metrics_redis():
    return get_redis()


@router.get("/metrics")
def metrics(client=Depends(get_metrics_redis)):
    return Response(content=render_metrics(client), media_type=metrics_content_type())