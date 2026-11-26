"""Сравнение выбранного источника, дневного выбора и доставки через хаб.

Дневной алгоритм списывает остаток и ёмкость только в копии состояния.
Цены за штуку, суммы за весь запрос и экономия передаются в копейках.
"""

from copy import deepcopy

import networkx as nx
from pydantic import BaseModel, Field

from app.modules.routing.daily import DayState, Decision, _van_candidate, _van_graph, choose
from app.modules.routing.models import Candidate, Leg, Order, Variant
from app.modules.routing.network import legs_of


class WhatIfRequest(BaseModel):
    source_pvz: str = Field(min_length=1)
    sku_id: str = Field(min_length=1)
    dest_pvz: str = Field(min_length=1)
    qty: int = Field(default=1, ge=1)


class WhatIfQuote(BaseModel):
    variant: Variant
    source_node: str
    reason: str
    money_kop: int | None
    total_money_kop: int | None
    hours: float | None
    saving_kop: int | None
    legs: list[Leg] = Field(default_factory=list)


class WhatIfDecision(Decision):
    total_money_kop: int | None
    saving_kop: int | None


class WhatIfResult(BaseModel):
    forced: WhatIfQuote
    best: WhatIfDecision
    hub: WhatIfQuote


def _quote(
    candidate: Candidate, order: Order, hub_kop: int, legs: list[Leg] | None = None
) -> WhatIfQuote:
    money = candidate.money_kop
    return WhatIfQuote(
        variant=candidate.variant,
        source_node=candidate.source_node,
        reason=candidate.reason,
        money_kop=money,
        total_money_kop=money * order.qty if money is not None else None,
        hours=candidate.hours,
        saving_kop=(hub_kop - money) * order.qty if money is not None else None,
        legs=legs or [],
    )


def _van_rejection(state: DayState, source: str, dest: str, default: str) -> str:
    # Нулевой порог сохраняет направление и пересадки, но не проверяет ёмкость и квоты.
    graph = _van_graph(state, 0)
    out_node, in_node = ("out", source), ("in", dest)
    if out_node in graph and in_node in graph and nx.has_path(graph, out_node, in_node):
        return f"{source} → {dest}: нет свободных мест или исчерпан лимит операций ПВЗ"

    source_routes = {r.id for r in state.routes if any(s.node_id == source for s in r.stops)}
    dest_routes = {r.id for r in state.routes if any(s.node_id == dest for s in r.stops)}
    common = source_routes & dest_routes
    if common:
        vans = ", ".join(sorted(common))
        return f"газель {vans} уже проехала {dest} к моменту загрузки в {source}"
    if source_routes and dest_routes:
        return f"{source} и {dest} обслуживают разные газели без доступной пересадки"
    return default


def _forced(state: DayState, source: str, order: Order, hub: Candidate) -> WhatIfQuote:
    p = state.params
    if not state.nodes[order.pvz_id].is_open:
        closed = hub.model_copy(
            update={
                "money_kop": None,
                "hours": None,
                "reason": f"{order.pvz_id} закрыт — заказ нельзя принять",
            }
        )
        return _quote(closed, order, p.hub_kop)

    if not state.nodes[source].is_open:
        reason = f"{source} закрыт"
    elif state.stock.get((source, order.sku_id), 0) < order.qty:
        reason = f"в {source} недостаточно свободного остатка для {order.qty} шт."
    elif source == order.pvz_id:
        own = Candidate(
            variant=Variant.OWN,
            source_node=source,
            money_kop=p.own_kop,
            hours=p.own_hours,
            reason="товар уже в ПВЗ покупателя",
        )
        return _quote(own, order, p.hub_kop)
    else:
        graph = _van_graph(state, order.qty)
        van = _van_candidate(state, graph, source, order)
        if van.money_kop is not None:
            path = nx.dijkstra_path(graph, ("out", source), ("in", order.pvz_id))
            return _quote(van, order, p.hub_kop, legs_of(path, {r.id: r for r in state.routes}))
        reason = _van_rejection(state, source, order.pvz_id, van.reason)

    return _quote(hub.model_copy(update={"reason": reason}), order, p.hub_kop)


def what_if(state: DayState, request: WhatIfRequest) -> WhatIfResult:
    """Применяет ограничение источника и обычный выбор к одному снимку данных."""
    snapshot = deepcopy(state)
    p = snapshot.params
    order = Order(id="what-if", sku_id=request.sku_id, qty=request.qty, pvz_id=request.dest_pvz)
    hub = Candidate(
        variant=Variant.HUB,
        source_node="HUB",
        money_kop=p.hub_kop,
        hours=p.hub_hours,
        reason="базовая схема: хаб + магистраль",
    )
    forced = _forced(snapshot, request.source_pvz, order, hub)
    decision = choose(snapshot, order)
    chosen = decision.assignment
    best = WhatIfDecision(
        order_id=decision.order_id,
        candidates=decision.candidates,
        assignment=chosen,
        total_money_kop=chosen.money_kop * order.qty if chosen else None,
        saving_kop=(p.hub_kop - chosen.money_kop) * order.qty if chosen else None,
    )
    return WhatIfResult(forced=forced, best=best, hub=_quote(hub, order, p.hub_kop))
