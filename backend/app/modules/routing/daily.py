"""Днём: быстрый выбор варианта для нового заказа и пересчёт при сбое.

Выбор (docs/03, «Днём при заказе»): свой ПВЗ → соседний → завтрашняя газель → хаб.
Для каждого варианта считаем «деньги + λ·часы», отбрасываем невозможные с причиной
и берём минимум. Выгода = хаб − вариант; если она ≤ 0 — заказ идёт через хаб.
"""

from dataclasses import dataclass

import networkx as nx
from pydantic import BaseModel

from app.modules.routing.models import (
    Assignment,
    Candidate,
    City,
    Leg,
    Node,
    NodeKind,
    Order,
    Params,
    VanRoute,
    Variant,
    is_unique_sku,
)
from app.modules.routing.network import Capacity, dist_km, hours_at, lam, legs_of


@dataclass
class DayState:
    """Что известно после ночного плана: маршруты, свободные места, остатки на полках."""

    params: Params
    nodes: dict[str, Node]
    routes: list[VanRoute]
    cap: Capacity
    stock: dict[tuple[str, str], int]  # (ПВЗ, артикул) → свободный остаток
    assignments: list[Assignment]


class Decision(BaseModel):
    order_id: str
    candidates: list[Candidate]
    assignment: Assignment | None


class RerouteDecision(BaseModel):
    order_id: str
    candidates: list[Candidate]
    extra_cost_kop: int
    extra_hours: float
    penalty_kop: int  # штраф ПВЗ, закрывшемуся без предупреждения
    notifications: list[str]


# ---------------------------------------------------------------- газель: кратчайший путь


def _van_graph(st: DayState, qty: int) -> nx.DiGraph:
    """Граф остановок газелей, где на каждом отрезке есть qty свободных мест."""
    p = st.params
    g = nx.DiGraph()
    for r in st.routes:
        for k, s in enumerate(r.stops):
            here = ("stop", r.id, k)
            if k + 1 < len(r.stops) and st.cap.seg_free[(r.id, k)] >= qty:
                g.add_edge(here, ("stop", r.id, k + 1), weight=0)
            if not st.nodes[s.node_id].is_open:
                continue
            if st.cap.out_left.get(s.node_id, 0) >= qty:
                g.add_edge(("out", s.node_id), here, weight=p.move_kop)
            if st.cap.in_left.get(s.node_id, 0) >= qty:
                g.add_edge(here, ("in", s.node_id), weight=lam(hours_at(s.eta_min, p), p))
            for r2 in st.routes:
                if r2.id == r.id:
                    continue
                for k2, s2 in enumerate(r2.stops):
                    if s2.node_id == s.node_id and s2.eta_min >= s.eta_min + p.transfer_min_gap:
                        g.add_edge(here, ("stop", r2.id, k2), weight=p.transfer_kop)
    return g


def _van_candidate(st: DayState, g: nx.DiGraph, source: str, o: Order) -> Candidate:
    p = st.params
    try:
        path = nx.dijkstra_path(g, ("out", source), ("in", o.pvz_id))
    except (nx.NodeNotFound, nx.NetworkXNoPath):
        return Candidate(
            variant=Variant.VAN,
            source_node=source,
            reason=f"нет попутной газели {source} → {o.pvz_id} "
            "(газель проходит их в обратном порядке или нет мест)",
        )
    legs = legs_of(path, {r.id: r for r in st.routes})
    money = p.own_kop + p.move_kop + p.transfer_kop * (len(legs) - 1)
    hours = round(hours_at(legs[-1].arrive_min, p), 2)
    via = " → ".join([legs[0].from_node] + [leg.to_node for leg in legs])
    return Candidate(
        variant=Variant.VAN,
        source_node=source,
        money_kop=money,
        hours=hours,
        reason=f"газель {', '.join(leg.route_id for leg in legs)}: {via}",
    )


# ---------------------------------------------------------------- выбор варианта


def hub_candidate(p: Params, sku_id: str) -> Candidate:
    if is_unique_sku(sku_id):
        return Candidate(
            variant=Variant.HUB, source_node="HUB", reason="в хабе нет уникального товара"
        )
    return Candidate(
        variant=Variant.HUB,
        source_node="HUB",
        money_kop=p.hub_kop,
        hours=p.hub_hours,
        reason="базовая схема: хаб + магистраль",
    )


def choose(st: DayState, o: Order) -> Decision:
    p = st.params
    cands: list[Candidate] = []
    pvz = st.nodes[o.pvz_id]
    hub = hub_candidate(p, o.sku_id)
    if not pvz.is_open:
        hub.reason = f"{o.pvz_id} закрыт — заказ нельзя принять"
        return Decision(order_id=o.id, candidates=[hub], assignment=None)

    def free(node_id: str) -> int:
        if is_unique_sku(o.sku_id) and st.nodes[node_id].kind != NodeKind.PVZ:
            return 0
        return st.stock.get((node_id, o.sku_id), 0)

    # 1. свой ПВЗ
    if free(o.pvz_id) >= o.qty:
        cands.append(
            Candidate(
                variant=Variant.OWN,
                source_node=o.pvz_id,
                money_kop=p.own_kop,
                hours=p.own_hours,
                reason="товар уже в ПВЗ покупателя",
            )
        )
    else:
        cands.append(
            Candidate(
                variant=Variant.OWN,
                source_node=o.pvz_id,
                reason=f"в {o.pvz_id} нет свободного остатка",
            )
        )

    sources = [
        n for n in st.nodes.values() if n.id != o.pvz_id and n.is_open and free(n.id) >= o.qty
    ]

    # 2. соседний ПВЗ
    for n in sources:
        if dist_km(n, pvz, p) > p.neighbor_radius_km:
            continue
        if o.accepts_neighbor:
            cands.append(
                Candidate(
                    variant=Variant.NEIGHBOR,
                    source_node=n.id,
                    money_kop=p.own_kop + p.neighbor_discount_kop,
                    hours=p.own_hours,
                    reason=f"забрать в соседнем {n.id}",
                )
            )
        else:
            cands.append(
                Candidate(
                    variant=Variant.NEIGHBOR,
                    source_node=n.id,
                    reason=f"покупатель не согласен забрать в {n.id}",
                )
            )

    # 3. попутная газель
    g = _van_graph(st, o.qty)
    for n in sources:
        cands.append(_van_candidate(st, g, n.id, o))

    # 4. отгрузка из хаба возможна для обычных артикулов.
    cands.append(hub)

    hub_gen = p.hub_kop + lam(p.hub_hours, p)
    for c in cands:
        if c.money_kop is None:
            continue
        if o.max_hours is not None and c.hours > o.max_hours:
            c.reason += f"; срок {c.hours} ч хуже обещанного {o.max_hours} ч"
            c.money_kop = None
            continue
        c.generalized_kop = c.money_kop + lam(c.hours, p)
        if hub.money_kop is not None:
            c.gain_vs_hub_kop = hub_gen - c.generalized_kop

    feasible = [c for c in cands if c.generalized_kop is not None]
    if not feasible:
        return Decision(order_id=o.id, candidates=cands, assignment=None)
    best = min(feasible, key=lambda c: (c.generalized_kop, c.variant != Variant.HUB))
    best.chosen = True
    for c in feasible:
        if c is not best:
            c.reason += (
                f"; дороже выбранного на {(c.generalized_kop - best.generalized_kop) / 100:.2f} ₽"
            )

    legs: list[Leg] = []
    if best.variant == Variant.VAN:
        path = nx.dijkstra_path(g, ("out", best.source_node), ("in", o.pvz_id))
        legs = legs_of(path, {r.id: r for r in st.routes})
        _consume_path(st, path, o.qty)
    if best.variant != Variant.HUB:
        st.stock[(best.source_node, o.sku_id)] -= o.qty
    a = Assignment(
        order_id=o.id,
        sku_id=o.sku_id,
        qty=o.qty,
        variant=best.variant,
        source_node=best.source_node,
        money_kop=best.money_kop,
        hours=best.hours,
        legs=legs,
    )
    st.assignments.append(a)
    return Decision(order_id=o.id, candidates=cands, assignment=a)


def _consume_path(st: DayState, path: list, qty: int) -> None:
    for a, b in zip(path, path[1:], strict=False):
        if a[0] == "out":
            st.cap.out_left[a[1]] -= qty
        elif b[0] == "in":
            st.cap.in_left[b[1]] -= qty
        elif a[0] == "stop" and b[0] == "stop" and a[1] == b[1]:
            st.cap.seg_free[(a[1], a[2])] -= qty


# ---------------------------------------------------------------- сбой: ПВЗ закрылся


def _stop_index(route: VanRoute, node_id: str, eta_min: int) -> int:
    return next(
        k for k, s in enumerate(route.stops) if s.node_id == node_id and s.eta_min == eta_min
    )


def close_pvz(
    st: DayState,
    node_id: str,
    buyer_accepts_alt: bool,
    penalty_per_unit_kop: int = 2000,
    now_min: int | None = None,
) -> list[RerouteDecision]:
    """Сценарий 4. ПВЗ закрылся — пересчитываем только пострадавшие заказы.

    now_min — минут с выезда газелей до инцидента; None — сразу после посадки товара.
      * товар едет в закрытый ПВЗ: выгрузить в другой ПВЗ той же газели, который она
        ещё не проехала (нужно согласие покупателя), или вернуть и довезти завтра;
      * товар забирают из закрытого ПВЗ или пересаживают в нём: путь не работает,
        заново выбираем вариант (другой источник, газель или хаб).
    Доп. затраты и штраф несёт закрывшийся ПВЗ.
    """
    st.nodes[node_id] = st.nodes[node_id].model_copy(update={"is_open": False})
    result = []
    for a in list(st.assignments):
        if a.variant != Variant.VAN or not a.legs:
            continue
        last = a.legs[-1]
        if last.to_node == node_id and (now_min is None or now_min < last.arrive_min):
            result.append(_unload_elsewhere(st, a, node_id, buyer_accepts_alt, now_min))
        elif any(
            leg.from_node == node_id and (now_min is None or now_min < leg.depart_min)
            for leg in a.legs
        ):
            result.append(_replan(st, a))
        else:
            continue
        result[-1].penalty_kop = penalty_per_unit_kop * a.qty
        reroute = result[-1]
        if any(candidate.chosen for candidate in reroute.candidates):
            reroute.notifications = [
                f"Покупателю: заказ {a.order_id} — {node_id} закрыт, "
                f"срок {reroute.extra_hours:+} ч",
                f"Продавцу: заказ {a.order_id} перемаршрутизирован, {node_id} закрыт",
            ]
        else:
            message = f"заказ {a.order_id} — {node_id} закрыт, доступного маршрута нет"
            reroute.notifications = [f"Покупателю: {message}", f"Продавцу: {message}"]
    return result


def _unload_elsewhere(
    st: DayState, a: Assignment, node_id: str, buyer_accepts_alt: bool, now_min: int | None
) -> RerouteDecision:
    p = st.params
    closed = st.nodes[node_id]
    last = a.legs[-1]
    r = next(r for r in st.routes if r.id == last.route_id)
    k_board = _stop_index(r, last.from_node, last.depart_min)
    k_closed = _stop_index(r, node_id, last.arrive_min)
    start = last.depart_min if now_min is None else max(now_min, last.depart_min)

    cands: list[tuple[float, int, Candidate]] = []  # (км до закрытого ПВЗ, остановка, вариант)
    for k, s in enumerate(r.stops):
        # только точки, которые газель ещё не проехала с товаром на борту
        if k <= k_board or k == k_closed or s.eta_min <= start:
            continue
        n = st.nodes[s.node_id]
        if not n.is_open:
            continue
        km = dist_km(n, closed, p)
        c = Candidate(
            variant=Variant.NEIGHBOR,
            source_node=s.node_id,
            money_kop=0,
            hours=max(0.0, round((s.eta_min - last.arrive_min) / 60, 2)),
            reason=f"выгрузить в {s.node_id} той же газелью, {km:.1f} км от {node_id}",
        )
        extra_segments = range(k_closed, k)  # товар едет дальше, чем планировали
        if any(st.cap.seg_free[(r.id, j)] < a.qty for j in extra_segments):
            c.reason += " — нет мест в газели дальше по маршруту"
            c.money_kop = None
        elif st.cap.in_left.get(s.node_id, 0) < a.qty:
            c.reason += " — ПВЗ исчерпал лимит mesh-операций"
            c.money_kop = None
        elif not buyer_accepts_alt:
            c.reason += " — покупатель не согласен"
            c.money_kop = None
        cands.append((km, k, c))
    cands.sort(key=lambda t: t[0])  # ближе к закрытому ПВЗ — удобнее покупателю
    alt = next(((k, c) for _, k, c in cands if c.money_kop is not None), None)

    tomorrow = Candidate(
        variant=Variant.VAN,
        source_node=r.depot_id,
        money_kop=p.move_kop,
        hours=24.0,
        reason="вернуть газелью и довезти завтрашним рейсом",
    )
    candidates = [c for _, _, c in cands] + [tomorrow]
    i = st.assignments.index(a)
    st.cap.in_left[node_id] += a.qty
    if alt:
        k, chosen = alt
        for j in range(k_closed, k):
            st.cap.seg_free[(r.id, j)] -= a.qty
        for j in range(k, k_closed):  # выгрузили раньше — место освободилось
            st.cap.seg_free[(r.id, j)] += a.qty
        st.cap.in_left[chosen.source_node] -= a.qty
        new_last = last.model_copy(
            update={"to_node": chosen.source_node, "arrive_min": r.stops[k].eta_min}
        )
    else:
        chosen = tomorrow
        new_last = last.model_copy(update={"to_node": r.depot_id})
    chosen.chosen = True
    st.assignments[i] = a.model_copy(
        update={"legs": a.legs[:-1] + [new_last], "hours": round(a.hours + chosen.hours, 2)}
    )
    return RerouteDecision(
        order_id=a.order_id,
        candidates=candidates,
        extra_cost_kop=chosen.money_kop * a.qty,
        extra_hours=chosen.hours,
        penalty_kop=0,
        notifications=[],
    )


def _replan(st: DayState, a: Assignment) -> RerouteDecision:
    """Вернуть места и остаток старого пути и выбрать вариант заново."""
    for leg in a.legs:
        r = next(r for r in st.routes if r.id == leg.route_id)
        k_from = _stop_index(r, leg.from_node, leg.depart_min)
        k_to = _stop_index(r, leg.to_node, leg.arrive_min)
        for j in range(k_from, k_to):
            st.cap.seg_free[(r.id, j)] += a.qty
    st.cap.out_left[a.legs[0].from_node] += a.qty
    st.cap.in_left[a.legs[-1].to_node] += a.qty
    # товар физически остался в ПВЗ-источнике (если тот закрыт, choose его не возьмёт)
    st.stock[(a.source_node, a.sku_id)] = st.stock.get((a.source_node, a.sku_id), 0) + a.qty
    st.assignments.remove(a)
    order = Order(id=a.order_id, sku_id=a.sku_id, qty=a.qty, pvz_id=a.legs[-1].to_node)
    decision = choose(st, order)
    new = decision.assignment
    return RerouteDecision(
        order_id=a.order_id,
        candidates=decision.candidates,
        extra_cost_kop=(new.money_kop - a.money_kop) * a.qty if new else 0,
        extra_hours=round(new.hours - a.hours, 2) if new else 0,
        penalty_kop=0,
        notifications=[],
    )


def day_state(
    city: City,
    plan_routes: list[VanRoute],
    cap: Capacity,
    stock_after: dict[tuple[str, str], int],
    assignments: list[Assignment],
) -> DayState:
    return DayState(
        params=city.params,
        nodes={n.id: n for n in city.nodes},
        routes=plan_routes,
        cap=cap,
        stock=dict(stock_after),
        assignments=list(assignments),
    )
