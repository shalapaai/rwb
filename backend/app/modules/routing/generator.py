"""Воспроизводимые синтетические города и сравнение трёх схем доставки.

Геометрия, остатки, заказы и прогноз генерируются отдельно от тарифов N8.
Отчёт использует действующий routing.compare и не меняет состояние приложения.
"""

from collections.abc import Callable, Iterable
from decimal import Decimal
from random import Random

from pydantic import BaseModel

from app.modules.economics.params import kopecks, routing_params
from app.modules.routing.compare import compare, nightly
from app.modules.routing.demo import demo_city
from app.modules.routing.models import (
    City,
    Forecast,
    Node,
    NodeKind,
    Order,
    Params,
    SchemeSummary,
    StockItem,
    Stop,
    VanRoute,
)
from app.modules.routing.network import dist_km, travel_min
from app.modules.routing.nightly import nightly_plan

SEEDS = tuple(range(20))
CITY_SIDE_KM = 15
VAN_CAPACITY = 300
BASE_STOP_MIN = 12  # длительность штатной выгрузки, как в демо


class Percentiles[T: (int, float)](BaseModel):
    median: T
    p10: T
    p90: T


class SchemeDistribution(BaseModel):
    scheme: str
    money_per_unit_kop: Percentiles[int]
    avg_hours: Percentiles[float]
    local_share: Percentiles[float]


class CityRun(BaseModel):
    seed: int
    pvz_count: int
    order_units: int
    schemes: list[SchemeSummary]


class NightlyOutlier(BaseModel):
    seed: int
    delta_kop: int  # ночной план минус по одному заказу, для всего города
    order_cost_delta_kop: int
    detour_cost_kop: int
    without_forecast_delta_kop: int
    greedy_hub_units: int
    nightly_hub_units: int
    reason: str


class RobustnessReport(BaseModel):
    seeds: list[int]
    schemes: list[SchemeDistribution]
    runs: list[CityRun]
    not_better_than_greedy: list[NightlyOutlier]


def _van_route(
    route_id: str, start: Node, pvzs: list[Node], hub: Node, params: Params, rng: Random
) -> VanRoute:
    """Каждая следующая остановка — ближайшая к текущей среди ещё не посещённых."""
    count = rng.randint(5, 15)
    left = [node for node in pvzs if node.id != start.id]
    visits = [start]
    for _ in range(count - 1):
        closest = min(left, key=lambda node: (dist_km(visits[-1], node, params), node.id))
        visits.append(closest)
        left.remove(closest)

    eta, previous = 0, hub
    stops = []
    for node in visits:
        eta += travel_min(dist_km(previous, node, params), params)
        stops.append(
            Stop(
                node_id=node.id,
                eta_min=eta,
                service_min=BASE_STOP_MIN,
                base_drop=rng.randint(1, VAN_CAPACITY // count),
            )
        )
        eta += BASE_STOP_MIN
        previous = node
    return VanRoute(id=route_id, depot_id=hub.id, capacity=VAN_CAPACITY, stops=stops)


def generate_city(seed: int) -> City:
    """Создаёт город 15×15 км. Начальный запас — уже принятые пригодные отказы."""
    rng = Random(seed)
    params = routing_params()
    # Два артикула из каталога демо сохраняют реальные для модели габариты.
    skus = demo_city(params).skus[:2]
    count = rng.randint(20, 40)
    pvzs = [
        Node(
            id=f"ПВЗ-{i:02d}", x_km=rng.uniform(0, CITY_SIDE_KM), y_km=rng.uniform(0, CITY_SIDE_KM)
        )
        for i in range(1, count + 1)
    ]
    hub = Node(id="HUB", kind=NodeKind.HUB, x_km=-CITY_SIDE_KM, y_km=CITY_SIDE_KM / 2)
    # Рейсы независимы: одни ПВЗ делят несколько газелей, другие ждут попутный крюк.
    starts = rng.sample(pvzs, 2)
    routes = [
        _van_route(f"G{i}", start, pvzs, hub, params, rng)
        for i, start in enumerate(starts, start=1)
    ]
    stock = [
        StockItem(node_id=node.id, sku_id=sku.id, qty=rng.randint(2, 8))
        for node in rng.sample(pvzs, rng.randint(count // 3, 2 * count // 3))
        for sku in rng.sample(skus, rng.randint(1, 2))
    ]
    orders = [
        Order(
            id=f"O-{i}",
            sku_id=rng.choice(skus).id,
            qty=rng.randint(1, 3),
            pvz_id=rng.choice(pvzs).id,
            accepts_neighbor=rng.choice((True, False)),
        )
        for i in range(1, rng.randint(count // 2, count) + 1)
    ]
    forecast = [
        Forecast(
            node_id=node.id,
            sku_id=rng.choice(skus).id,
            daily_mean=rng.uniform(0.3, 2.0),
            daily_std=rng.uniform(0.1, 1.0),
        )
        for node in rng.sample(pvzs, rng.randint(count // 2, count))
    ]
    return City(
        nodes=[hub, *pvzs],
        routes=routes,
        skus=skus,
        stock=stock,
        orders=orders,
        forecast=forecast,
        params=params,
    )


def _percentiles[T: (int, float)](
    values: Iterable[T], convert: Callable[[Decimal], T]
) -> Percentiles[T]:
    """Линейная интерполяция по позиции (n−1)·p, деньги округляются до копейки."""
    ordered = sorted(Decimal(str(value)) for value in values)

    def at(percent: int) -> T:
        index, fraction = divmod((len(ordered) - 1) * percent, 100)
        following = min(index + 1, len(ordered) - 1)
        return convert((ordered[index] * (100 - fraction) + ordered[following] * fraction) / 100)

    return Percentiles(median=at(50), p10=at(10), p90=at(90))


def _explain(
    seed: int, city: City, greedy: SchemeSummary, planned: SchemeSummary
) -> NightlyOutlier:
    plan, _ = nightly_plan(city)
    detours = sum(stop.cost_kop for stop in plan.inserted_stops)
    gap = planned.money_kop - greedy.money_kop
    orders_gap = gap - detours
    without_forecast = (
        nightly(city.model_copy(update={"forecast": []})).money_kop - greedy.money_kop
    )
    explanation = (
        f"Разница по заказам {orders_gap:+d} коп., крюки {detours} коп. "
        f"Через хаб прошло {planned.hub_units} ед. в ночном плане и {greedy.hub_units} ед. "
        f"при расчёте по одному заказу. Без прогноза разница с ним {without_forecast:+d} коп."
    )
    return NightlyOutlier(
        seed=seed,
        delta_kop=gap,
        order_cost_delta_kop=orders_gap,
        detour_cost_kop=detours,
        without_forecast_delta_kop=without_forecast,
        greedy_hub_units=greedy.hub_units,
        nightly_hub_units=planned.hub_units,
        reason=explanation,
    )


def robustness_report() -> RobustnessReport:
    runs = []
    outliers = []
    for seed in SEEDS:
        city = generate_city(seed)
        hub, greedy, planned = compare(city)
        runs.append(
            CityRun(
                seed=seed,
                pvz_count=len(city.nodes) - 1,
                order_units=sum(order.qty for order in city.orders),
                schemes=[hub, greedy, planned],
            )
        )
        if planned.money_kop >= greedy.money_kop:
            outliers.append(_explain(seed, city, greedy, planned))

    schemes = [
        SchemeDistribution(
            scheme=results[0].scheme,
            money_per_unit_kop=_percentiles((r.money_per_unit_kop for r in results), kopecks),
            avg_hours=_percentiles((r.avg_hours for r in results), float),
            local_share=_percentiles((r.local_share for r in results), float),
        )
        for results in zip(*(run.schemes for run in runs), strict=True)
    ]
    return RobustnessReport(
        seeds=list(SEEDS), schemes=schemes, runs=runs, not_better_than_greedy=outliers
    )
