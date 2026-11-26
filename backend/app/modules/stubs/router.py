"""ИМИТАЦИЯ хаба и магистрали — внешней системы, которую по заданию не реализуем.

Цена и срок — из economics.params, одним агрегатом: разбивки по сегментам в модели
нет, поэтому сегменты показаны без цен. Каждый ответ помечен imitation: true,
чтобы UI показывал плашку «ИМИТАЦИЯ» (правило AGENTS.md).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import Role, require_roles
from app.core.db import get_session
from app.modules.economics.params import DEFAULTS as P
from app.modules.orders.models import Order

router = APIRouter(prefix="/stub", tags=["stubs"])

AnyRole = Annotated[Role, Depends(require_roles(*Role))]
Db = Annotated[Session, Depends(get_session)]
LABEL = "ИМИТАЦИЯ"


class Segment(BaseModel):
    name: str
    imitation: bool = True
    label: str = LABEL


class HubQuote(BaseModel):
    imitation: bool = True
    label: str = LABEL
    price_kop: int  # заказ из хаба целиком: хаб + магистраль + рейс до ПВЗ
    hours: float
    refusal_return_kop: int  # если отказ везут обратно в хаб (базовая схема)
    segments: list[Segment]


class HubShipment(HubQuote):
    order_id: int
    pvz_id: str
    status: str


SEGMENTS = ["Хаб: отбор и упаковка", "Магистраль до города", "Рейс газели хаб → ПВЗ"]


def _quote() -> dict:
    return {
        "price_kop": P.hub_order_kop,
        "hours": P.hub_delivery_min / 60,
        "refusal_return_kop": P.hub_return_kop,
        "segments": [Segment(name=s) for s in SEGMENTS],
    }


@router.get("/hub", response_model=HubQuote)
def hub_quote(_: AnyRole) -> dict:
    """Сколько стоит и сколько идёт заказ через хаб и магистраль."""
    return _quote()


@router.get("/hub/orders/{order_id}", response_model=HubShipment)
def hub_shipment(order_id: int, _: AnyRole, db: Db) -> dict:
    """Отправка из хаба по заказу с вариантом «хаб»: сегменты помечены «ИМИТАЦИЯ»."""
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(404, f"Нет заказа {order_id}")
    if order.variant != "hub":
        raise HTTPException(409, f"Заказ {order_id} исполняется без хаба: {order.variant}")
    return {**_quote(), "order_id": order.id, "pvz_id": order.pvz_id, "status": order.status}
