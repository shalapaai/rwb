"""Ночной план mesh (docs/03-architecture.md, «Маршрутизация»).

Шаги:
  1. граф «дешёвых» рёбер по завтрашним маршрутам газелей      — network.build_graph
  2. целевой запас по ПВЗ из прогноза (формула «газетчика»)       — targets()
  3. поток минимальной стоимости по каждому артикулу             — network.solve_sku
  4. правка маршрутов: вставить ПВЗ в рейс, только если окупается — insertion_options()
  5. повтор: вставленный заезд становится «бесплатным» для всех   — цикл в nightly_plan()
  6. выход: маршруты, назначения заказов, переброски, крюки

Артикулы решаются по очереди (сначала самые востребованные), и каждый забирает
свою часть мест в газелях. Это упрощение задачи о многопродуктовом потоке:
точное решение — в плане развития (OR-Tools, ALNS).
"""

import math
from dataclasses import dataclass

from app.modules.routing.models import (
    Assignment,
    City,
    InsertedStop,
    Movement,
    Node,
    NodeKind,
    Plan,
    Stop,
    VanRoute,
    Variant,
)
from app.modules.routing.network import (
    Capacity,
    SkuProblem,
    dist_km,
    route_duration_min,
    solve_sku,
    travel_min,
)

# ---------------------------------------------------------------- шаг 2


def targets(city: City, nodes: dict[str, Node]) -> dict[tuple[str, str], int]:
    """Сколько штук артикула выгодно держать в ПВЗ: S = d·T + z·σ·√T, не больше полки."""
    p = city.params
    volume = {s.id: s.volume_l for s in city.skus}
    out: dict[tuple[str, str], int] = {}
    for f in city.forecast:
        node = nodes[f.node_id]
        if not node.is_open:
            continue
        s = f.daily_mean * p.cover_days + p.safety_z * f.daily_std * math.sqrt(p.cover_days)
        shelf_limit = int(node.shelf_free_l // volume[f.sku_id])
        out[(f.node_id, f.sku_id)] = min(math.ceil(s), shelf_limit)
    return out


# ---------------------------------------------------------------- шаг 3 для всех артикулов


@dataclass
class Evaluation:
    assignments: list[Assignment]
    movements: list[Movement]
    cost: int  # целевая функция без крюков
    cap: Capacity
    stock_after: dict[tuple[str, str], int]  # свободно на полках после плана
    incoming: dict[tuple[str, str], int]  # едет газелью под прогноз, на полку ещё не лёг


def evaluate(city: City, routes: list[VanRoute], nodes: dict[str, Node]) -> Evaluation:
    cap = Capacity.from_routes(routes, nodes, city.params)
    tgt = targets(city, nodes)
    stock: dict[tuple[str, str], int] = {}
    for s in city.stock:
        stock[(s.node_id, s.sku_id)] = stock.get((s.node_id, s.sku_id), 0) + s.qty

    volume = {sku.id: sku.volume_l for sku in city.skus}
    demand = {sku.id: 0 for sku in city.skus}
    for o in city.orders:
        demand[o.sku_id] += o.qty
    sku_order = sorted(demand, key=lambda s: (-demand[s], s))  # сначала самые востребованные

    assignments: list[Assignment] = []
    movements: list[Movement] = []
    cost = 0
    for sku_id in sku_order:
        sku_stock = {n: q for (n, s), q in stock.items() if s == sku_id}
        prob = SkuProblem(
            sku_id=sku_id,
            stock=sku_stock,
            orders=[o for o in city.orders if o.sku_id == sku_id],
            volume_l=volume[sku_id],
            target={n: t for (n, s), t in tgt.items() if s == sku_id},
        )
        res = solve_sku(city, routes, nodes, cap, prob)
        assignments += res.assignments
        movements += res.movements
        cost += res.cost

    stock_after = dict(stock)
    incoming: dict[tuple[str, str], int] = {}
    for a in assignments:
        if a.variant != Variant.HUB:
            stock_after[(a.source_node, a.sku_id)] -= a.qty
    for m in movements:
        stock_after[(m.from_node, m.sku_id)] -= m.qty
        incoming[(m.to_node, m.sku_id)] = incoming.get((m.to_node, m.sku_id), 0) + m.qty
    return Evaluation(assignments, movements, cost, cap, stock_after, incoming)


# ---------------------------------------------------------------- шаг 4


@dataclass
class Insertion:
    route_id: str
    node_id: str
    position: int  # индекс в списке остановок, куда встаёт новый ПВЗ
    eta_min: int  # когда газель приедет в новый ПВЗ
    added_km: float
    added_min: int
    cost_kop: int


def insertion_options(
    city: City, routes: list[VanRoute], nodes: dict[str, Node], base_duration: dict[str, int]
) -> list[Insertion]:
    """Для каждой газели и каждого ПВЗ не на её маршруте — самое дешёвое место вставки.

    Ограничения (docs/02, «Граница выгодности»): к рейсу добавляется не больше
    min(20 мин, 10% длительности), не больше 2 новых остановок.
    """
    p = city.params
    options = []
    for r in routes:
        if sum(s.added for s in r.stops) >= p.max_new_stops:
            continue
        limit = min(p.max_added_min_abs, p.max_added_share * base_duration[r.id])
        already = route_duration_min(r, nodes, p) - base_duration[r.id]
        on_route = {s.node_id for s in r.stops}
        chain = [r.depot_id] + [s.node_id for s in r.stops] + [r.depot_id]
        # когда газель уезжает из точки chain[pos]: из депо — в 0, с остановки — после стоянки
        depart = [0] + [s.eta_min + s.service_min for s in r.stops]
        for x in nodes.values():
            if x.kind != NodeKind.PVZ or not x.is_open or x.id in on_route:
                continue
            best = None
            for pos in range(len(chain) - 1):
                a, b = nodes[chain[pos]], nodes[chain[pos + 1]]
                km = dist_km(a, x, p) + dist_km(x, b, p) - dist_km(a, b, p)
                if best is None or km < best[1]:
                    best = (pos, km)
            pos, km = best
            a, b = nodes[chain[pos]], nodes[chain[pos + 1]]
            to_x = travel_min(dist_km(a, x, p), p)
            # насколько позже станут все следующие остановки (и возврат в депо)
            minutes = (
                to_x
                + p.stop_service_min
                + travel_min(dist_km(x, b, p), p)
                - travel_min(dist_km(a, b, p), p)
            )
            if already + minutes > limit:
                continue
            cost = round(km * p.van_kop_per_km + minutes * p.van_kop_per_min)
            eta = depart[pos] + to_x
            options.append(Insertion(r.id, x.id, pos, eta, round(km, 2), minutes, cost))
    return options


def apply_insertion(routes: list[VanRoute], ins: Insertion, city: City) -> list[VanRoute]:
    """Новая копия маршрутов со вставленной остановкой; время следующих остановок сдвигается."""
    out = []
    for r in routes:
        if r.id != ins.route_id:
            out.append(r)
            continue
        stops = [s.model_copy() for s in r.stops]
        for s in stops[ins.position :]:
            s.eta_min += ins.added_min
        new = Stop(
            node_id=ins.node_id,
            eta_min=ins.eta_min,
            service_min=city.params.stop_service_min,
            added=True,
        )
        stops.insert(ins.position, new)
        out.append(r.model_copy(update={"stops": stops}))
    return out


# ---------------------------------------------------------------- шаги 4–6


def nightly_plan(city: City) -> tuple[Plan, Evaluation]:
    p = city.params
    nodes = {n.id: n for n in city.nodes}
    routes = [r.model_copy(deep=True) for r in city.routes]
    base_duration = {r.id: route_duration_min(r, nodes, p) for r in routes}

    current = evaluate(city, routes, nodes)
    inserted: list[InsertedStop] = []
    detour_cost = 0

    for _ in range(p.max_iterations):  # шаг 5: повторы
        best = None
        for ins in insertion_options(city, routes, nodes, base_duration):
            trial_routes = apply_insertion(routes, ins, city)
            trial = evaluate(city, trial_routes, nodes)
            gain = current.cost - (
                trial.cost + ins.cost_kop
            )  # крюк окупается, только если gain > 0
            if gain > 0 and (best is None or gain > best[0]):
                best = (gain, ins, trial_routes, trial)
        if best is None:
            break
        gain, ins, routes, current = best
        detour_cost += ins.cost_kop
        inserted.append(
            InsertedStop(
                route_id=ins.route_id,
                node_id=ins.node_id,
                position=ins.position,
                added_km=ins.added_km,
                added_min=ins.added_min,
                cost_kop=ins.cost_kop,
                gain_kop=gain,
            )
        )

    money = (
        sum(a.money_kop * a.qty for a in current.assignments)
        + sum(m.money_kop * m.qty for m in current.movements)
        + detour_cost
    )
    plan = Plan(
        routes=routes,
        assignments=current.assignments,
        movements=current.movements,
        inserted_stops=inserted,
        total_money_kop=money,
        hub_money_kop=sum(o.qty for o in city.orders) * p.hub_kop,
        objective_kop=current.cost + detour_cost,
    )
    return plan, current
