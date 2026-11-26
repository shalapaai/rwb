"""Ночной план mesh (docs/03-architecture.md, «Маршрутизация»).

Шаги:
  1. граф «дешёвых» рёбер по завтрашним маршрутам газелей      — network.build_graph
  2. целевой запас по ПВЗ из прогноза (формула «газетчика»)       — targets()
  3. потоки минимальной стоимости с общими ёмкостями              — allocation.allocate_flows
  4. правка маршрутов: вставить ПВЗ в рейс, только если окупается — insertion_options()
  5. повтор: вставленный заезд становится «бесплатным» для всех   — цикл в nightly_plan()
  6. выход: маршруты, назначения заказов, переброски, крюки

Потоки артикулов согласуются по местам в газелях, операциям ПВЗ и полкам.
"""

import math
from dataclasses import dataclass

from app.modules.routing.allocation import allocate_flows
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
    is_unique_sku,
)
from app.modules.routing.network import (
    Capacity,
    SkuProblem,
    build_graph,
    decode_sku_flow,
    dist_km,
    route_duration_min,
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
    unassigned: dict[str, int]
    forecast_saving_kop: int

    @property
    def money_kop(self) -> int:
        return sum(a.money_kop * a.qty for a in self.assignments) + sum(
            m.money_kop * m.qty for m in self.movements
        )

def evaluate(city: City, routes: list[VanRoute], nodes: dict[str, Node]) -> Evaluation:
    cap = Capacity.from_routes(routes, nodes, city.params, next_routes=city.routes)
    tgt = targets(city, nodes)
    stock: dict[tuple[str, str], int] = {}
    for s in city.stock:
        if is_unique_sku(s.sku_id) and nodes[s.node_id].kind != NodeKind.PVZ:
            continue
        stock[(s.node_id, s.sku_id)] = stock.get((s.node_id, s.sku_id), 0) + s.qty

    volume = {sku.id: sku.volume_l for sku in city.skus}
    demand = {sku.id: 0 for sku in city.skus}
    for o in city.orders:
        demand[o.sku_id] += o.qty
    sku_order = sorted(demand, key=lambda s: (not is_unique_sku(s), -demand[s], s))

    assignments: list[Assignment] = []
    movements: list[Movement] = []
    unassigned: dict[str, int] = {}
    cost = 0
    problems = [
        SkuProblem(
            sku_id=sku_id,
            stock={n: q for (n, s), q in stock.items() if s == sku_id},
            orders=[o for o in city.orders if o.sku_id == sku_id],
            volume_l=volume[sku_id],
            target={n: t for (n, s), t in tgt.items() if s == sku_id},
        )
        for sku_id in sku_order
    ]
    graphs = [build_graph(city, routes, nodes, cap, problem) for problem in problems]
    flows = allocate_flows(graphs, cap)
    for problem, graph, flow in zip(problems, graphs, flows, strict=True):
        res = decode_sku_flow(city, routes, nodes, problem, graph, flow)
        assignments += res.assignments
        movements += res.movements
        unassigned.update(res.unassigned)
        cost += res.cost

    stock_after = dict(stock)
    incoming: dict[tuple[str, str], int] = {}
    for a in assignments:
        if a.variant != Variant.HUB or a.legs:
            stock_after[(a.source_node, a.sku_id)] -= a.qty
    for m in movements:
        stock_after[(m.from_node, m.sku_id)] -= m.qty
        incoming[(m.to_node, m.sku_id)] = incoming.get((m.to_node, m.sku_id), 0) + m.qty
    forecast_units = sum(
        min(target, stock_after.get(key, 0) + incoming.get(key, 0))
        for key, target in tgt.items() if target > 0
    )
    forecast_saving = forecast_units * max(city.params.rebalance_reward_kop, 0)
    return Evaluation(
        assignments, movements, cost, cap, stock_after, incoming, unassigned, forecast_saving
    )


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
    """Новые остановки и возможные повторные заезды в ПВЗ, где ждут заказ.

    Ограничения (docs/02, «Граница выгодности»): к рейсу добавляется не больше
    min(20 мин, 10% длительности), не больше 2 новых остановок.
    """
    p = city.params
    destinations = {o.pvz_id for o in city.orders}
    useful = destinations | {s.node_id for s in city.stock if s.qty} | {
        f.node_id for f in city.forecast
    } | {s.node_id for r in routes for s in r.stops}
    options = []
    for r in routes:
        if sum(s.added for s in r.stops) >= p.max_new_stops:
            continue
        limit = min(p.max_added_min_abs, p.max_added_share * base_duration[r.id])
        already = route_duration_min(r, nodes, p) - base_duration[r.id]
        last_visit = {s.node_id: k for k, s in enumerate(r.stops)}
        added_nodes = {s.node_id for s in r.stops if s.added}
        chain = [r.depot_id] + [s.node_id for s in r.stops] + [r.depot_id]
        # когда газель уезжает из точки chain[pos]: из депо — в 0, с остановки — после стоянки
        depart = [0] + [s.eta_min + s.service_min for s in r.stops]
        for x in nodes.values():
            if x.id not in useful or x.kind != NodeKind.PVZ or not x.is_open:
                continue
            previous = last_visit.get(x.id)
            if previous is not None and (x.id not in destinations or x.id in added_nodes):
                continue
            # Между визитами должна быть остановка, где можно забрать товар.
            first = 0 if previous is None else previous + 2
            positions = []
            for pos in range(first, len(chain) - 1):
                a, b = nodes[chain[pos]], nodes[chain[pos + 1]]
                km = dist_km(a, x, p) + dist_km(x, b, p) - dist_km(a, b, p)
                positions.append((km, pos))
            for km, pos in sorted(positions):
                a, b = nodes[chain[pos]], nodes[chain[pos + 1]]
                to_x = travel_min(dist_km(a, x, p), p)
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
                if previous is not None and eta <= r.stops[previous].eta_min:
                    continue  # визиты в один ПВЗ должны различаться по времени
                if pos < len(r.stops) and eta + p.stop_service_min > r.stops[pos].eta_min + minutes:
                    continue  # обслуживание новой остановки не накладывается на следующую
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
        waiting = sum(current.unassigned.values())
        for ins in insertion_options(city, routes, nodes, base_duration):
            trial_routes = apply_insertion(routes, ins, city)
            trial = evaluate(city, trial_routes, nodes)
            extra_delivered = waiting - sum(trial.unassigned.values())
            gain = current.cost - (trial.cost + ins.cost_kop)
            # Заезд окупают текущие заказы или дополнительная экономия от прогноза.
            # Уже ожидаемые продажи учтены в целевой функции обоих планов.
            future_gain = max(0, trial.forecast_saving_kop - current.forecast_saving_kop)
            saving = current.money_kop - trial.money_kop - ins.cost_kop + future_gain
            if extra_delivered == 0 and saving <= 0:
                continue
            rank = (extra_delivered, gain)
            if rank > (0, 0) and (best is None or rank > best[0]):
                best = (rank, ins, trial_routes, trial)
        if best is None:
            break
        (_, gain), ins, routes, current = best
        detour_cost += ins.cost_kop
        for stop in inserted:
            if stop.route_id == ins.route_id and stop.position >= ins.position:
                stop.position += 1  # позиция должна указывать на визит в итоговом маршруте
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

    plan = Plan(
        routes=routes,
        assignments=current.assignments,
        movements=current.movements,
        inserted_stops=inserted,
        unassigned=current.unassigned,
        total_money_kop=current.money_kop + detour_cost,
        hub_money_kop=sum(o.qty for o in city.orders if not is_unique_sku(o.sku_id)) * p.hub_kop,
        objective_kop=current.cost + detour_cost,
    )
    return plan, current
