import time
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import Role, current_role
from app.core.db import get_session

router = APIRouter(tags=["system"])

_started_at = time.monotonic()
_request_count = 0


def count_request() -> None:
    global _request_count
    _request_count += 1


@router.get("/health")
def health(session: Annotated[Session, Depends(get_session)]) -> dict:
    try:
        session.execute(text("select 1"))
        db = "ok"
    except Exception:
        db = "unavailable"
    return {"status": "ok" if db == "ok" else "degraded", "db": db}


@router.get("/metrics", response_class=PlainTextResponse)
def metrics() -> str:
    """Минимальные метрики в формате Prometheus."""
    uptime = time.monotonic() - _started_at
    return (
        "# TYPE app_uptime_seconds gauge\n"
        f"app_uptime_seconds {uptime:.0f}\n"
        "# TYPE app_requests_total counter\n"
        f"app_requests_total {_request_count}\n"
    )


@router.get("/me")
def me(role: Annotated[Role, Depends(current_role)]) -> dict:
    return {"role": role}
