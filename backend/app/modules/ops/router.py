"""Нештатные ситуации: закрытие ПВЗ, тестовые часы, уведомления."""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import Role, require_roles
from app.core.db import get_session
from app.core.idempotency import IdemKey, run_once
from app.modules.ops import service
from app.modules.ops.models import Notification

router = APIRouter(prefix="", tags=["ops"])

Logist = Annotated[Role, Depends(require_roles(Role.LOGIST, Role.ADMIN))]
Admin = Annotated[Role, Depends(require_roles(Role.ADMIN))]
Operator = Annotated[Role, Depends(require_roles(Role.PVZ_OPERATOR, Role.ADMIN))]
AnyRole = Annotated[Role, Depends(require_roles(*Role))]
Db = Annotated[Session, Depends(get_session)]


class IncidentIn(BaseModel):
    node_id: str
    buyer_accepts_alt: bool = False  # покупатель согласен забрать в другом ПВЗ (заглушка)
    now_min: int | None = None  # минут с выезда газелей; None — сразу после погрузки


class ClockIn(BaseModel):
    days: int = Field(ge=1, le=60)


class NextDayIn(BaseModel):
    expected_day: date


@router.post("/incidents")
def incident(body: IncidentIn, key: IdemKey, _: Logist, db: Db) -> dict[str, Any]:
    """Сценарий 4: ПВЗ закрылся, товар для него уже в газели."""
    result = run_once(
        db,
        f"incident:{body.node_id}",
        key,
        lambda: service.close_pvz(db, body.node_id, body.buyer_accepts_alt, body.now_min),
    )
    db.commit()
    return result


@router.post("/sim/clock")
def clock(body: ClockIn, key: IdemKey, _: Admin, db: Db) -> dict[str, Any]:
    """Сценарий 5: сдвинуть тестовое время; уведомления на 7-й день, решение на 14-й."""
    result = run_once(db, "sim-clock", key, lambda: service.shift_clock(db, body.days))
    db.commit()
    return result


@router.post("/sim/clock/next")
def next_day(body: NextDayIn, key: IdemKey, _: Operator, db: Db) -> dict[str, Any]:
    """Перейти с показанной оператору даты на следующий день для всех ПВЗ."""
    result = run_once(db, "sim-next", key, lambda: service.shift_clock(db, 1, body.expected_day))
    db.commit()
    return result


@router.get("/notifications")
def notifications(_: AnyRole, db: Db, role: str | None = None) -> list[dict[str, Any]]:
    """Уведомления в UI вместо SMS и ботов (правило организаторов)."""
    q = select(Notification).order_by(Notification.id.desc()).limit(100)
    if role:
        q = q.where(Notification.role == role)
    return [
        {
            "id": n.id,
            "role": n.role,
            "order_id": n.order_id,
            "text": n.text,
            "created_at": n.created_at.isoformat(),
        }
        for n in db.scalars(q)
    ]
