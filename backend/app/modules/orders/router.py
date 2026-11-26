"""Эндпоинты заказов: создание, действия ПВЗ по шагам, задания ПВЗ.

Изменяющие запросы требуют Idempotency-Key; действия — ещё и версию заказа.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import Role, require_roles
from app.core.db import get_session
from app.core.idempotency import IdemKey, run_once
from app.modules.orders import service
from app.modules.orders.models import Order
from app.modules.orders.schemas import OrderIn, OrderOut, TasksOut, VersionIn

router = APIRouter(tags=["orders"])

Buyer = Annotated[Role, Depends(require_roles(Role.ADMIN, Role.LOGIST))]  # заказ — заглушка
Operator = Annotated[Role, Depends(require_roles(Role.PVZ_OPERATOR, Role.ADMIN))]
AnyRole = Annotated[Role, Depends(require_roles(*Role))]
Db = Annotated[Session, Depends(get_session)]
Action = Literal["pick", "load", "receive", "issue", "cancel"]


def _out(o: Order) -> dict:
    return OrderOut.model_validate(
        {
            **{c: getattr(o, c) for c in OrderOut.model_fields if c not in ("candidates",)},
            "candidates": o.candidates or [],
        }
    ).model_dump(mode="json")


@router.post("/orders", response_model=OrderOut)
def create(body: OrderIn, key: IdemKey, _: Buyer, db: Db):
    """Новый заказ (тестовая форма вместо приложения покупателя): выбор варианта и резерв."""
    result = run_once(
        db,
        "order",
        key,
        lambda: _out(
            service.create_order(db, body.sku_id, body.pvz_id, body.accepts_neighbor, body.qty)
        ),
    )
    db.commit()
    return result


@router.get("/orders/{order_id}", response_model=OrderOut)
def get(order_id: int, _: AnyRole, db: Db):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(404, f"Нет заказа {order_id}")
    return _out(order)


@router.post("/orders/{order_id}/{action}", response_model=OrderOut)
def act(order_id: int, action: Action, body: VersionIn, key: IdemKey, _: Operator, db: Db):
    """Шаг ПВЗ: pick — отобрать, load — отдать в газель, receive — принять, issue — выдать."""
    result = run_once(
        db,
        f"order:{order_id}:{action}",
        key,
        lambda: _out(service.act(db, order_id, action, body.version)),
    )
    db.commit()
    return result


@router.get("/pvz/{pvz_id}/tasks", response_model=TasksOut)
def tasks(pvz_id: str, _: AnyRole, db: Db):
    """Что ПВЗ должен сделать: отобрать, отдать в газель, принять, выдать."""
    return service.tasks(db, pvz_id)
