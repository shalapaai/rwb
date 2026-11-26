"""Граф «дешёвых» рёбер и поток минимальной стоимости (шаги 1 и 3 ночного плана).

Идея. Каждая остановка газели — узел графа ("stop", маршрут, номер остановки).
Товар может:
  * остаться на полке и уйти покупателю этого же ПВЗ           — вариант OWN;
  * уйти покупателю соседнего ПВЗ, если тот согласен            — NEIGHBOR;
  * сесть в газель на остановке A, проехать по её маршруту
    (и, возможно, пересесть в другую газель) и выйти в ПВЗ B   — VAN;
  * отсутствовать в городе — тогда заказ закрывает хаб          — HUB.

Все варианты — рёбра графа с ценой «деньги + λ·часы». Метки на рёбрах задают
общие для всех артикулов ёмкости кузовов, операций ПВЗ и полок.

Переброска под прогноз: у каждого ПВЗ есть узел цели ("tgt", ПВЗ) с ёмкостью
«целевой запас». Каждая единица, которая к вечеру лежит в ПВЗ в пределах цели —
своя или привезённая, — получает «награду» (отрицательная цена) = ожидаемая
экономия. Поэтому поток сам перевозит товар из излишка в дефицит, если награда
больше цены перевозки, а привезти можно не больше, чем влезет на свободную полку.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

import networkx as nx

from app.modules.routing.models import (
    Assignment,
    City,
    Leg,
    Movement,
    Node,
    NodeKind,
    Order,
    Params,
    VanRoute,
    Variant,
    is_unique_sku,
)

HUB = "HUB"  # источник «бесконечного» товара базовой схемы
KEEP = "KEEP"  # сток: товар, который остался лежать
UNASSIGNED = "UNASSIGNED"  # неудовлетворённый спрос, без отгрузки и списания
DAY_MIN = 24 * 60
type ResourceKey = str | tuple[str, int]
type Resource = tuple[str, ResourceKey]


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


def _segment_capacity(routes: list[VanRoute]) -> dict[tuple[str, int], int]:
    free: dict[tuple[str, int], int] = {}
    for route in routes:
        load = sum(stop.base_drop for stop in route.stops)
        free[route.id, -1] = max(0, route.capacity - load)  # от выезда из хаба
        for index, stop in enumerate(route.stops):
            load -= stop.base_drop
            free[route.id, index] = max(0, route.capacity - load)
    return free


@dataclass
class Capacity:
    """Что ещё свободно: места в газелях по отрезкам, mesh-операции и полки ПВЗ."""

    seg_free: dict[tuple[str, int], int]  # (маршрут, k) → мест на отрезке после остановки k
    out_left: dict[str, int]  # сколько единиц ПВЗ ещё может отдать в газели
    in_left: dict[str, int]  # сколько единиц ПВЗ ещё может принять
    shelf_left_l: dict[str, float]  # литров свободно на mesh-полке — общий лимит для всех артикулов
    next_seg_free: dict[tuple[str, int], int] = field(default_factory=dict)
    next_in_left: dict[str, int] = field(default_factory=dict)

    def remaining(self, resource: Resource) -> Decimal:
        pool, key = resource
        return Decimal(str(getattr(self, pool)[key]))

    def consume(self, resource: Resource, amount: Decimal) -> None:
        pool, key = resource
        remaining = self.remaining(resource) - amount
        getattr(self, pool)[key] = float(remaining) if pool == "shelf_left_l" else int(remaining)

    @classmethod
    def from_routes(
        cls, routes: list[VanRoute], nodes: dict[str, Node], p: Params,
        *, next_routes: list[VanRoute] | None = None,
    ):
        pvz = [n.id for n in nodes.values() if n.kind == "pvz"]
        return cls(
            seg_free=_segment_capacity(routes),
            out_left={n: p.pvz_daily_ops_limit for n in pvz},
            in_left={n: p.pvz_daily_ops_limit for n in pvz},
            shelf_left_l={n: nodes[n].shelf_free_l for n in pvz},
            next_seg_free=_segment_capacity(next_routes) if next_routes else {},
            next_in_left={n: p.pvz_daily_ops_limit for n in pvz} if next_routes else {},
        )


# ---------------------------------------------------------------- граф


@dataclass
class SkuProblem:
    sku_id: str
    stock: dict[str, int]  # ПВЗ → штук
    orders: list[Order]
    volume_l: float = 2.0
    target: dict[str, int] = field(default_factory=dict)  # ПВЗ → сколько выгодно держать


def _capacity_edge(
    graph: nx.DiGraph, cap: Capacity, source, dest, resource: Resource, *,
    weight: int = 0, units: Decimal = Decimal(1),
) -> None:
    graph.add_edge(
        source, dest, weight=weight, capacity=int(cap.remaining(resource) // units),
        resource=resource, units=units,
    )


def _meets_deadline(order: Order, hours: float) -> bool:
    return order.max_hours is None or hours <= order.max_hours


def _add_hub_relay(
    g: nx.DiGraph, city: City, routes: list[VanRoute], nodes: dict[str, Node],
    cap: Capacity, orders_by_pvz: dict[str, list[Order]],
) -> None:
    """Товар для завтрашней отгрузки должен приехать с полки тем же потоком."""
    p = city.params
    hubs = {node.id for node in nodes.values() if node.kind == NodeKind.HUB and node.is_open}
    for route in routes:
        if route.depot_id not in hubs or not route.stops:
            continue
        last = len(route.stops) - 1
        if route_duration_min(route, nodes, p) >= DAY_MIN:
            continue
        # Отдельный отрезок после последнего ПВЗ использует сегодняшнюю ёмкость кузова.
        back = ("return", route.id, last)
        _capacity_edge(g, cap, ("stop", route.id, last), back, ("seg_free", (route.id, last)))
        g.add_edge(back, ("relay", route.depot_id), weight=p.hub_kop)

    for route in city.routes:
        depot = ("relay", route.depot_id)
        if depot not in g or (route.id, -1) not in cap.next_seg_free:
            continue
        # Завтра повторяется базовое расписание, загрузка считается отдельно от сегодняшней.
        previous = depot
        for k, stop in enumerate(route.stops):
            visit = ("tomorrow", route.id, k)
            _capacity_edge(g, cap, previous, visit, ("next_seg_free", (route.id, k - 1)))
            previous = visit
            arrival_hours = hours_at(DAY_MIN + stop.eta_min, p)
            for order in orders_by_pvz.get(stop.node_id, []):
                if _meets_deadline(order, arrival_hours):
                    _capacity_edge(
                        g, cap, visit, ("order", order.id), ("next_in_left", stop.node_id),
                        weight=lam(arrival_hours, p),
                    )


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
    unique = is_unique_sku(prob.sku_id)
    g.add_node(UNASSIGNED, demand=-order_qty)
    g.add_edge(UNASSIGNED, KEEP, weight=0)
    if not unique:
        g.add_edge(UNASSIGNED, HUB, weight=0)
    g.add_node(KEEP, demand=total_supply)  # всё, что не ушло в заказы, остаётся лежать

    # --- заказы: свой ПВЗ, соседний, хаб
    orders_by_pvz = defaultdict(list)
    for o in prob.orders:
        on = ("order", o.id)
        g.add_node(on, demand=o.qty)
        if nodes[o.pvz_id].kind != NodeKind.PVZ or not is_open(o.pvz_id):
            continue
        orders_by_pvz[o.pvz_id].append(o)
        if not unique and _meets_deadline(o, p.hub_hours):
            g.add_edge(HUB, on, weight=p.hub_kop + lam(p.hub_hours, p))
        if not _meets_deadline(o, p.own_hours):
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
    for r in routes if total_supply else []:
        for k, s in enumerate(r.stops):
            here = ("stop", r.id, k)
            if k + 1 < len(r.stops):  # проезд к следующей остановке (и мимо закрытого ПВЗ)
                _capacity_edge(g, cap, here, ("stop", r.id, k + 1), ("seg_free", (r.id, k)))
            if not is_open(s.node_id):
                continue  # в закрытом ПВЗ не садимся, не выходим и не пересаживаемся
            if prob.stock.get(s.node_id, 0) > 0:  # посадка
                _capacity_edge(
                    g, cap, ("stock", s.node_id), ("out", s.node_id), ("out_left", s.node_id),
                )
                g.add_edge(("out", s.node_id), here, weight=p.move_kop)
            # Срок проверяется для каждого визита: ранний и поздний рейсы не смешиваются.
            arrival_hours = hours_at(s.eta_min, p)
            arrival_cost = lam(arrival_hours, p)
            for order in orders_by_pvz.get(s.node_id, []):
                if _meets_deadline(order, arrival_hours):
                    _capacity_edge(
                        g, cap, here, ("order", order.id), ("in_left", s.node_id),
                        weight=p.own_kop + arrival_cost,
                    )
            if prob.target.get(s.node_id, 0) > 0:
                g.add_edge(here, ("in", s.node_id), weight=arrival_cost)
            for r2 in routes:  # пересадка в другую газель в этом же ПВЗ
                if r2.id == r.id:
                    continue
                for k2, s2 in enumerate(r2.stops):
                    if s2.node_id == s.node_id and s2.eta_min >= s.eta_min + p.transfer_min_gap:
                        g.add_edge(here, ("stop", r2.id, k2), weight=p.transfer_kop)

    for node_id in {s.node_id for r in routes for s in r.stops}:
        if not is_open(node_id) or ("in", node_id) not in g:
            continue
        _capacity_edge(g, cap, ("in", node_id), ("in2", node_id), ("in_left", node_id))
        _capacity_edge(
            g, cap, ("in2", node_id), ("tgt", node_id), ("shelf_left_l", node_id),
            units=Decimal(str(prob.volume_l)),
        )
    if unique and total_supply and orders_by_pvz:
        _add_hub_relay(g, city, routes, nodes, cap, orders_by_pvz)
    # Штраф больше разницы стоимости двух потоков: приоритет у числа исполненных единиц.
    weight_bound = sum(abs(edge["weight"]) for _, _, edge in g.edges(data=True))
    penalty = weight_bound * (total_supply + order_qty) + 1
    for order in prob.orders:
        g.add_edge(UNASSIGNED, ("order", order.id), weight=penalty)
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


def _extend_legs_through_hub(
    legs: list[Leg], path: list, city: City, routes: dict[str, VanRoute], nodes: dict[str, Node]
) -> None:
    inbound = routes[legs[-1].route_id]
    legs[-1] = legs[-1].model_copy(update={
        "to_node": inbound.depot_id,
        "arrive_min": route_duration_min(inbound, nodes, city.params),
    })
    _, route_id, stop_index = next(n for n in reversed(path) if n[0] == "tomorrow")
    outbound = next(route for route in city.routes if route.id == route_id)
    stop = outbound.stops[stop_index]
    legs.append(Leg(
        route_id=route_id,
        from_node=outbound.depot_id,
        to_node=stop.node_id,
        depart_min=DAY_MIN,
        arrive_min=DAY_MIN + stop.eta_min,
    ))


@dataclass
class SkuResult:
    assignments: list[Assignment]
    movements: list[Movement]
    cost: int  # значение целевой функции для этого артикула
    unassigned: dict[str, int]


def decode_sku_flow(
    city: City, routes: list[VanRoute], nodes: dict[str, Node], prob: SkuProblem,
    g: nx.DiGraph, flow: dict,
) -> SkuResult:
    """Раскладывает согласованный поток артикула на назначения и переброски."""
    p = city.params
    cost = nx.cost_of_flow(g, flow)
    by_route = {r.id: r for r in routes}
    orders = {o.id: o for o in prob.orders}
    assignments: list[Assignment] = []
    movements: list[Movement] = []
    unassigned: dict[str, int] = {}

    for path, qty in decompose(flow, g):
        head, tail = path[0], path[-1]
        if head == UNASSIGNED:
            if tail == KEEP:
                continue
            if path[1] != HUB:
                unassigned[tail[1]] = qty
                cost -= g[head][tail]["weight"] * qty
                continue
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
            via_hub = any(node[0] == "relay" for node in path)
            if via_hub:
                _extend_legs_through_hub(legs, path, city, by_route, nodes)
            assignments.append(
                Assignment(
                    order_id=o.id,
                    sku_id=prob.sku_id,
                    qty=qty,
                    variant=Variant.HUB if via_hub else Variant.VAN,
                    source_node=source,
                    money_kop=(p.hub_kop if via_hub else p.own_kop) + move_money,
                    hours=round(hours_at(legs[-1].arrive_min, p), 2),
                    legs=legs,
                )
            )
    return SkuResult(assignments, movements, cost, unassigned)
