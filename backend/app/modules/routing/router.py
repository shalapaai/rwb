"""API логистики.

`POST /routing/plan` — чистый расчёт: город на входе, ночной план на выходе.
`/routing/demo/*` — демо-город в памяти процесса для показа сценариев в UI;
хранение в БД — задача модулей inventory/orders.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.core.auth import Role, require_roles
from app.modules.routing.compare import compare
from app.modules.routing.daily import (
    DayState,
    Decision,
    RerouteDecision,
    choose,
    close_pvz,
    day_state,
)
from app.modules.routing.demo import demo_city
from app.modules.routing.models import City, Order, Plan, SchemeSummary
from app.modules.routing.nightly import nightly_plan

router = APIRouter(prefix="/routing", tags=["routing"])

Planner = Annotated[Role, Depends(require_roles(Role.LOGIST, Role.ADMIN))]
AnyRole = Annotated[Role, Depends(require_roles(*Role))]


class DemoState(BaseModel):
    city: City
    plan: Plan


_demo: dict[str, object] = {}
_replies: dict[str, object] = {}  # ключ идемпотентности → уже отданный ответ

IdemKey = Annotated[str | None, Header(alias="Idempotency-Key")]


def _once(key: str | None, action):
    """Повтор запроса с тем же ключом не меняет состояние и возвращает прежний ответ."""
    if key is None:
        return action()
    if key not in _replies:
        _replies[key] = action()
    return _replies[key]


def _state() -> tuple[DemoState, DayState]:
    if "day" not in _demo:
        reset_demo_state()
    return _demo["view"], _demo["day"]  # type: ignore[return-value]


def reset_demo_state() -> DemoState:
    city = demo_city()
    plan, ev = nightly_plan(city)
    view = DemoState(city=city, plan=plan)
    _demo["view"] = view
    _replies.clear()
    _demo["day"] = day_state(city, plan.routes, ev.cap, ev.stock_after, plan.assignments)
    return view


@router.post("/plan", response_model=Plan)
def plan(city: City, _: Planner) -> Plan:
    """Ночной план для произвольного города."""
    return nightly_plan(city)[0]


@router.post("/compare", response_model=list[SchemeSummary])
def compare_schemes(city: City, _: Planner) -> list[SchemeSummary]:
    """Три схемы на одних заказах: всё через хаб, по одному заказу, ночной план."""
    return compare(city)


@router.post("/demo/reset", response_model=DemoState)
def demo_reset(_: AnyRole) -> DemoState:
    """Пересоздать демо-город и построить ночной план (повтор безопасен сам по себе)."""
    return reset_demo_state()


@router.get("/demo", response_model=DemoState)
def demo_get(_: AnyRole) -> DemoState:
    return _state()[0]


@router.get("/demo/compare", response_model=list[SchemeSummary])
def demo_compare(_: AnyRole) -> list[SchemeSummary]:
    return compare(demo_city())


@router.post("/demo/orders", response_model=Decision)
def demo_order(order: Order, _: AnyRole, key: IdemKey = None) -> Decision:
    """Новый заказ днём: кандидаты с причинами и выбранный вариант."""
    _, day = _state()
    if order.pvz_id not in day.nodes:
        raise HTTPException(404, f"Нет ПВЗ {order.pvz_id}")
    if any(a.order_id == order.id for a in day.assignments) and key not in _replies:
        raise HTTPException(409, f"Заказ {order.id} уже есть")
    return _once(key, lambda: choose(day, order))


@router.post("/demo/incidents/{node_id}", response_model=list[RerouteDecision])
def demo_incident(
    node_id: str,
    _: AnyRole,
    buyer_accepts_alt: bool = False,
    now_min: int | None = None,
    key: IdemKey = None,
) -> list[RerouteDecision]:
    """Сценарий 4: ПВЗ закрылся, товар для него уже в газели."""
    _, day = _state()
    if node_id not in day.nodes:
        raise HTTPException(404, f"Нет ПВЗ {node_id}")
    return _once(key, lambda: close_pvz(day, node_id, buyer_accepts_alt, now_min=now_min))
