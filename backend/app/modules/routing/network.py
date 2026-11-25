"""Граф «дешёвых» рёбер и поток минимальной стоимости (шаги 1 и 3 ночного плана).

Идея. Каждая остановка газели — узел графа ("stop", маршрут, номер остановки).
Товар может:
  * остаться на полке и уйти покупателю этого же ПВЗ           — вариант OWN;
  * уйти покупателю соседнего ПВЗ, если тот согласен            — NEIGHBOR;
  * сесть в газель на остановке A, проехать по её маршруту
    (и, возможно, пересесть в другую газель) и выйти в ПВЗ B   — VAN;
  * отсутствовать в городе — тогда заказ закрывает хаб          — HUB.

Все варианты — рёбра одного графа с ценой «деньги + λ·часы», поэтому
networkx.min_cost_flow сам выбирает самый дешёвый способ сразу для всех заказов
одного артикула. Встречные перевозки при этом не возникают: поток минимальной
стоимости их не содержит.

Переброска под прогноз: у каждого ПВЗ есть узел цели ("tgt", ПВЗ) с ёмкостью
«целевой запас». Каждая единица, которая к вечеру лежит в ПВЗ в пределах цели —
своя или привезённая, — получает «награду» (отрицательная цена) = ожидаемая
экономия. Поэтому поток сам перевозит товар из излишка в дефицит, если награда
больше цены перевозки, а привезти можно не больше, чем влезет на свободную полку.
"""

import math
from dataclasses import dataclass, field

import networkx as nx

from app.modules.routing.models import (
    Assignment,
    City,
    Leg,
    Movement,
    Node,
    Order,
    Params,
    VanRoute,
    Variant,
)

HUB = "HUB"  # источник «бесконечного» товара базовой схемы
KEEP = "KEEP"  # сток: товар, который остался лежать


# ---------------------------------------------------------------- геометрия


def dist_km(a: Node, b: Node, p: Params) -> float:
    """Расстояние по дороге: прямая × коэффициент извилистости."""
    return math.hypot(a.x_km - b.x_km, a.y_km - b.y_km) * p.road_factor


def travel_min(km: float, p: Params) -> int:
    return round(km / p.speed_kmh * 60 * p.season_factor)


def route_duration_min(route: VanRoute, nodes: dict[str, Node], p: Params) -> int:
    """Время рейса до возвращения в депо."""
    if not route.stops:
        return 0
    last = route.stops[-1]
    back = travel_min(dist_km(nodes[last.node_id], nodes[route.depot_id], p), p)
    return last.eta_min + last.service_min + back


def hours_at(eta_min: int, p: Params) -> float:
    """Через сколько часов после заказа товар окажется в ПВЗ, если газель привезёт его к eta."""
    return p.hours_until_routes + eta_min / 60


def lam(hours: float, p: Params) -> int:
    return round(hours * p.lambda_kop_per_hour)


# ---------------------------------------------------------------- ёмкости


@dataclass
class Capacity:
    """Что ещё свободно: места в газелях по отрезкам, mesh-операции и полки ПВЗ."""

    seg_free: dict[tuple[str, int], int]  # (маршрут, k) → мест на отрезке после остановки k
    out_left: dict[str, int]  # сколько единиц ПВЗ ещё может отдать в газели
    in_left: dict[str, int]  # сколько единиц ПВЗ ещё может принять
    shelf_left_l: dict[str, float]  # литров свободно на mesh-полке — общий лимит для всех артикулов

    @classmethod
    def from_routes(cls, routes: list[VanRoute], nodes: dict[str, Node], p: Params):
        seg_free: dict[tuple[str, int], int] = {}
        for r in routes:
            load = sum(s.base_drop for s in r.stops)  # газель выезжает загруженной заказами из хаба
            for k, s in enumerate(r.stops):
                load -= s.base_drop  # …и освобождается по ходу маршрута
                seg_free[(r.id, k)] = max(0, r.capacity - load)
        pvz = [n.id for n in nodes.values() if n.kind == "pvz"]
        return cls(
            seg_free=seg_free,
            out_left={n: p.pvz_daily_ops_limit for n in pvz},
            in_left={n: p.pvz_daily_ops_limit for n in pvz},
            shelf_left_l={n: nodes[n].shelf_free_l for n in pvz},
        )


# ---------------------------------------------------------------- граф


@dataclass
class SkuProblem:
    sku_id: str
    stock: dict[str, int]  # ПВЗ → штук
    orders: list[Order]
    volume_l: float = 2.0
    target: dict[str, int] = field(default_factory=dict)  # ПВЗ → сколько выгодно держать


def build_graph(
    city: City,
    routes: list[VanRoute],
    nodes: dict[str, Node],
    cap: Capacity,
    prob: SkuProblem,
) -> nx.DiGraph:
    p = city.params
    reward = p.rebalance_reward_kop
    g = nx.DiGraph()

    def is_open(node_id: str) -> bool:
        return nodes[node_id].is_open

    # --- источники и сток
    total_supply = 0
    for node_id, qty in prob.stock.items():
        if qty <= 0:
            continue
        g.add_node(("stock", node_id), demand=-qty)
        total_supply += qty
        if prob.target.get(node_id, 0) > 0:  # оставить на месте в пределах цели
            g.add_edge(("stock", node_id), ("tgt", node_id), weight=0)
        g.add_edge(("stock", node_id), KEEP, weight=0)
    for node_id, t in prob.target.items():
        if t > 0 and is_open(node_id):  # каждая единица в пределах цели — награда
            g.add_edge(("tgt", node_id), KEEP, capacity=t, weight=-reward)

    order_qty = sum(o.qty for o in prob.orders)
    g.add_node(HUB, demand=-order_qty)
    g.add_edge(HUB, KEEP, weight=0)
    g.add_node(KEEP, demand=total_supply)  # всё, что не ушло в заказы, остаётся лежать

    # --- заказы: свой ПВЗ, соседний, хаб
    for o in prob.orders:
        on = ("order", o.id)
        g.add_node(on, demand=o.qty)
        g.add_edge(HUB, on, weight=p.hub_kop + lam(p.hub_hours, p))
        if not is_open(o.pvz_id):
            continue
        if prob.stock.get(o.pvz_id, 0) > 0:
            g.add_edge(("stock", o.pvz_id), on, weight=p.own_kop + lam(p.own_hours, p))
        if o.accepts_neighbor:
            for n_id, qty in prob.stock.items():
                if n_id == o.pvz_id or qty <= 0 or not is_open(n_id):
                    continue
                if dist_km(nodes[n_id], nodes[o.pvz_id], p) <= p.neighbor_radius_km:
                    w = p.own_kop + p.neighbor_discount_kop + lam(p.own_hours, p)
                    g.add_edge(("stock", n_id), on, weight=w)

    # --- газели: посадка, проезд, пересадка, высадка
    for r in routes:
        for k, s in enumerate(r.stops):
            here = ("stop", r.id, k)
            if k + 1 < len(r.stops):  # проезд к следующей остановке (и мимо закрытого ПВЗ)
                g.add_edge(here, ("stop", r.id, k + 1), capacity=cap.seg_free[(r.id, k)], weight=0)
            if not is_open(s.node_id):
                continue  # в закрытом ПВЗ не садимся, не выходим и не пересаживаемся
            if prob.stock.get(s.node_id, 0) > 0:  # посадка
                g.add_edge(
                    ("stock", s.node_id),
                    ("out", s.node_id),
                    capacity=cap.out_left[s.node_id],
                    weight=0,
                )
                g.add_edge(("out", s.node_id), here, weight=p.move_kop)
            # высадка: в ПВЗ, где ждут заказ или где дефицит
            g.add_edge(here, ("in", s.node_id), weight=lam(hours_at(s.eta_min, p), p))
            for r2 in routes:  # пересадка в другую газель в этом же ПВЗ
                if r2.id == r.id:
                    continue
                for k2, s2 in enumerate(r2.stops):
                    if s2.node_id == s.node_id and s2.eta_min >= s.eta_min + p.transfer_min_gap:
                        g.add_edge(here, ("stop", r2.id, k2), weight=p.transfer_kop)

    for node_id in {s.node_id for r in routes for s in r.stops}:
        if not is_open(node_id) or ("in", node_id) not in g:
            continue
        g.add_edge(("in", node_id), ("in2", node_id), capacity=cap.in_left[node_id], weight=0)
        for o in prob.orders:
            if o.pvz_id == node_id:
                g.add_edge(("in2", node_id), ("order", o.id), weight=p.own_kop)
        shelf_units = int(cap.shelf_left_l.get(node_id, 0) // prob.volume_l)
        if prob.target.get(node_id, 0) > 0 and shelf_units > 0:  # привезти под прогноз
            g.add_edge(("in2", node_id), ("tgt", node_id), capacity=shelf_units, weight=0)
    return g


# ---------------------------------------------------------------- разбор потока


def decompose(flow: dict, g: nx.DiGraph) -> list[tuple[list, int]]:
    """Раскладывает поток на пути «источник → сток» с количеством штук."""
    flow = {u: dict(vs) for u, vs in flow.items()}
    paths = []
    sources = [n for n, d in g.nodes(data="demand") if d and d < 0]
    for s in sources:
        while sum(flow[s].values()) > 0:
            path, u = [s], s
            while u != KEEP and not (isinstance(u, tuple) and u[0] == "order"):
                u = next(v for v, f in flow[u].items() if f > 0)
                path.append(u)
            amount = min(flow[a][b] for a, b in zip(path, path[1:], strict=False))
            for a, b in zip(path, path[1:], strict=False):
                flow[a][b] -= amount
            paths.append((path, amount))
    return paths


def legs_of(path: list, routes: dict[str, VanRoute]) -> list[Leg]:
    """Отрезки пути по газелям: где сел, где вышел или пересел."""
    stops = [n for n in path if isinstance(n, tuple) and n[0] == "stop"]
    legs: list[Leg] = []
    start = stops[0]
    for prev, cur in zip(stops, stops[1:] + [None], strict=False):
        if cur is None or cur[1] != prev[1]:
            r = routes[prev[1]]
            a, b = r.stops[start[2]], r.stops[prev[2]]
            legs.append(
                Leg(
                    route_id=r.id,
                    from_node=a.node_id,
                    to_node=b.node_id,
                    depart_min=a.eta_min,
                    arrive_min=b.eta_min,
                )
            )
            start = cur
    return legs


@dataclass
class SkuResult:
    assignments: list[Assignment]
    movements: list[Movement]
    cost: int  # значение целевой функции для этого артикула


def solve_sku(
    city: City, routes: list[VanRoute], nodes: dict[str, Node], cap: Capacity, prob: SkuProblem
) -> SkuResult:
    """Шаг 3: поток минимальной стоимости по одному артикулу; списывает занятую ёмкость."""
    p = city.params
    g = build_graph(city, routes, nodes, cap, prob)
    flow = nx.min_cost_flow(g)
    cost = nx.cost_of_flow(g, flow)
    by_route = {r.id: r for r in routes}
    orders = {o.id: o for o in prob.orders}
    assignments: list[Assignment] = []
    movements: list[Movement] = []

    for path, qty in decompose(flow, g):
        _consume(path, qty, cap)
        head, tail = path[0], path[-1]
        if head == HUB:
            if tail != KEEP:
                o = orders[tail[1]]
                assignments.append(
                    Assignment(
                        order_id=o.id,
                        sku_id=prob.sku_id,
                        qty=qty,
                        variant=Variant.HUB,
                        source_node=HUB,
                        money_kop=p.hub_kop,
                        hours=p.hub_hours,
                    )
                )
            continue
        source = head[1]
        uses_van = any(isinstance(n, tuple) and n[0] == "stop" for n in path)
        if not uses_van:
            if tail == KEEP:
                continue  # остался лежать
            o = orders[tail[1]]
            own = source == o.pvz_id
            assignments.append(
                Assignment(
                    order_id=o.id,
                    sku_id=prob.sku_id,
                    qty=qty,
                    variant=Variant.OWN if own else Variant.NEIGHBOR,
                    source_node=source,
                    money_kop=p.own_kop + (0 if own else p.neighbor_discount_kop),
                    hours=p.own_hours,
                )
            )
            continue
        legs = legs_of(path, by_route)
        move_money = p.move_kop + p.transfer_kop * (len(legs) - 1)
        if tail == KEEP:
            cap.shelf_left_l[legs[-1].to_node] -= qty * prob.volume_l  # заняли полку
            movements.append(
                Movement(
                    sku_id=prob.sku_id,
                    qty=qty,
                    from_node=source,
                    to_node=legs[-1].to_node,
                    legs=legs,
                    money_kop=move_money,
                )
            )
        else:
            o = orders[tail[1]]
            assignments.append(
                Assignment(
                    order_id=o.id,
                    sku_id=prob.sku_id,
                    qty=qty,
                    variant=Variant.VAN,
                    source_node=source,
                    money_kop=p.own_kop + move_money,
                    hours=round(hours_at(legs[-1].arrive_min, p), 2),
                    legs=legs,
                )
            )
    return SkuResult(assignments, movements, cost)


def _consume(path: list, qty: int, cap: Capacity) -> None:
    for a, b in zip(path, path[1:], strict=False):
        if isinstance(a, tuple) and isinstance(b, tuple):
            if a[0] == "stop" and b[0] == "stop" and a[1] == b[1]:
                cap.seg_free[(a[1], a[2])] -= qty
            elif a[0] == "stock" and b[0] == "out":
                cap.out_left[a[1]] -= qty
            elif a[0] == "in" and b[0] == "in2":
                cap.in_left[a[1]] -= qty
