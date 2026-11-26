"""Пограничные сценарии дневной и ночной маршрутизации.

Матрица демо-карты проверяет API, малые графы изолируют ограничения и сбои.
Регрессионные проверки сверяют сроки, деньги и доступность маршрутов.
"""

from collections.abc import Iterator
from itertools import combinations, product

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.routing import router as demo_router
from app.modules.routing.daily import choose
from app.modules.routing.models import (
    City,
    Forecast,
    NodeKind,
    Order,
    Sku,
    StockItem,
    Stop,
    Variant,
)
from app.modules.routing.network import dist_km, route_duration_min, travel_min
from app.modules.routing.nightly import nightly_plan
from tests.routing.test_routing import _day, city, route

HEADERS = {"X-Role": "logist"}


@pytest.fixture
def demo_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(demo_router, "_demo", {})
    monkeypatch.setattr(demo_router, "_replies", {})
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_what_if_for_every_ordered_pair_on_demo_map(demo_client: TestClient) -> None:
    response = demo_client.get("/routing/demo", headers=HEADERS)
    assert response.status_code == 200
    demo = response.json()
    graph = City.model_validate(demo["city"])
    pvzs = [n.id for n in graph.nodes if n.kind == NodeKind.PVZ]
    final_routes = {
        r["id"]: [s["node_id"] for s in r["stops"]] for r in demo["plan"]["routes"]
    }
    forward = {
        (source, dest): route_id
        for route_id, stops in final_routes.items()
        for source, dest in combinations(stops, 2)
    }
    assert len(pvzs) == 11
    for source, dest in product(pvzs, repeat=2):
        response = demo_client.post(
            "/routing/what-if",
            headers=HEADERS,
            json={"source_pvz": source, "dest_pvz": dest, "sku_id": f"UNIQUE-{source}"},
        )
        assert response.status_code == 200, (source, dest, response.text)
        data = response.json()
        forced, best = data["forced"], data["best"]["assignment"]
        pair = (source, dest)
        assert data["hub"]["total_money_kop"] is None, pair
        if source == dest:
            assert best["variant"] == forced["variant"] == Variant.OWN, pair
            assert best["money_kop"] == graph.params.own_kop, pair
        elif pair in forward:
            assert best["variant"] == forced["variant"] == Variant.VAN, pair
            (leg,) = best["legs"]
            assert (leg["from_node"], leg["to_node"], leg["route_id"]) == (
                source, dest, forward[pair]
            )
            assert leg["arrive_min"] > leg["depart_min"], pair
            assert best["money_kop"] == graph.params.own_kop + graph.params.move_kop, pair
        else:
            assert best is None and forced["total_money_kop"] is None, pair


@pytest.mark.parametrize(
    ("gap_delta", "transfer_open", "expected"),
    [(-1, True, Variant.HUB), (0, True, Variant.VAN), (0, False, Variant.HUB)],
    ids=["на-минуту-раньше", "ровно-порог", "закрыта-точка-пересадки"],
)
def test_transfer_time_and_closure_boundary(
    gap_delta: int, transfer_open: bool, expected: Variant
) -> None:
    order = Order(id="o", sku_id="S", pvz_id="B")
    c = city([], [("A", 1)], [order])
    arrive = 80
    depart = arrive + c.params.transfer_min_gap + gap_delta
    c.routes = [route("R1", [("A", 60), ("T", arrive)]), route("R2", [("T", depart), ("B", 120)])]
    c.nodes = [
        n.model_copy(update={"is_open": transfer_open}) if n.id == "T" else n for n in c.nodes
    ]
    c.params.max_new_stops = 0

    (night,) = nightly_plan(c)[0].assignments
    day = choose(_day(c), order).assignment
    assert day is not None
    assert night.variant == day.variant == expected
    if expected == Variant.VAN:
        assert [(leg.route_id, leg.from_node, leg.to_node) for leg in night.legs] == [
            ("R1", "A", "T"), ("R2", "T", "B")
        ]
        assert night.legs == day.legs
        assert night.money_kop == day.money_kop == (
            c.params.own_kop + c.params.move_kop + c.params.transfer_kop
        )


def test_base_deliveries_free_capacity_for_the_next_segment() -> None:
    r = route("R", [("A", 60), ("B", 90), ("X", 120)], capacity=2)
    r.stops[0].base_drop = r.stops[1].base_drop = 1
    first = Order(id="first", sku_id="S", pvz_id="B", qty=2)
    second = Order(id="second", sku_id="S2", pvz_id="X", qty=2)
    c = city([r], [("A", 2)], [first])
    c.skus.append(Sku(id="S2"))
    c.stock.append(StockItem(node_id="B", sku_id="S2", qty=2))
    c.orders.append(second)
    c.params.max_new_stops = 0

    plan, ev = nightly_plan(c)
    assert {(a.order_id, a.variant): a.qty for a in plan.assignments} == {
        ("first", Variant.VAN): 1,
        ("first", Variant.HUB): 1,
        ("second", Variant.VAN): 2,
    }
    assert [ev.cap.seg_free["R", i] for i in (-1, 0, 1)] == [0, 0, 0]


def test_daily_uses_hub_at_exact_break_even() -> None:
    c = city([route("R", [("A", 60), ("B", 120)])], [("A", 1)], [])
    # Нулевая оценка времени изолирует границу денежной выгоды.
    c.params.lambda_kop_per_hour = 0
    c.params.hub_kop = c.params.own_kop + c.params.move_kop
    st = _day(c)
    decision = choose(st, Order(id="o", sku_id="S", pvz_id="B"))
    (van,) = [candidate for candidate in decision.candidates if candidate.variant == Variant.VAN]

    assert van.gain_vs_hub_kop == 0
    assert decision.assignment is not None and decision.assignment.variant == Variant.HUB
    assert st.stock["A", "S"] == 1


def test_nightly_respects_order_deadline() -> None:
    order = Order(id="o", sku_id="S", pvz_id="B", max_hours=11)
    c = city([route("R", [("A", 60), ("B", 120)])], [("A", 1)], [order])
    c.params.max_new_stops = 0

    assert choose(_day(c), order).assignment is None
    plan, _ = nightly_plan(c)
    assert not plan.assignments
    assert plan.unassigned == {order.id: order.qty}


def test_each_order_only_uses_visits_that_meet_its_deadline() -> None:
    routes = [
        route("early", [("A", 60), ("B", 120)], capacity=1),
        route("late", [("A", 60), ("B", 180)], capacity=1),
    ]
    orders = [
        Order(id="flexible", sku_id="S", pvz_id="B", max_hours=13),
        Order(id="strict", sku_id="S", pvz_id="B", max_hours=12),
    ]
    c = city(routes, [("A", 2)], orders)
    c.params.max_new_stops = 0

    plan, _ = nightly_plan(c)
    assert plan.unassigned == {}
    assert {a.order_id: (a.legs[0].route_id, a.hours) for a in plan.assignments} == {
        "flexible": ("late", 13), "strict": ("early", 12),
    }


def test_nightly_cannot_deliver_to_closed_destination() -> None:
    order = Order(id="o", sku_id="S", pvz_id="B")
    c = city([route("R", [("A", 60), ("B", 120)])], [("A", 1)], [order])
    c.nodes = [n.model_copy(update={"is_open": False}) if n.id == "B" else n for n in c.nodes]

    assert choose(_day(c), order).assignment is None
    plan, _ = nightly_plan(c)
    assert not plan.assignments, plan.assignments
    assert plan.unassigned == {order.id: order.qty}


def _off_route_city(x_km: float, y_km: float, qty: int) -> City:
    order = Order(id="o", sku_id="S", pvz_id="X", qty=qty)
    c = city([route("R", [("A", 60), ("B", 120)])], [("A", qty)], [order])
    c.nodes = [
        n.model_copy(update={"x_km": x_km, "y_km": y_km}) if n.id == "X" else n
        for n in c.nodes
    ]
    return c


def test_new_stop_must_save_money_against_hub() -> None:
    plan, _ = nightly_plan(_off_route_city(0.5, 0.5, qty=1))

    assert not plan.inserted_stops
    assert plan.total_money_kop == plan.hub_money_kop
    assert [(a.variant, a.qty) for a in plan.assignments] == [(Variant.HUB, 1)]


def test_new_stop_can_pay_off_through_forecast_sales() -> None:
    c = _off_route_city(0.5, 0.15, qty=4)
    c.orders = []
    c.forecast = [Forecast(node_id="X", sku_id="S", daily_mean=2)]

    plan, ev = nightly_plan(c)
    (stop,) = plan.inserted_stops
    assert stop.node_id == "X"
    (movement,) = plan.movements
    assert (movement.from_node, movement.to_node, movement.qty) == ("A", "X", 4)
    assert plan.total_money_kop < ev.forecast_saving_kop == 4 * c.params.rebalance_reward_kop


def test_new_stop_considers_profitable_position_after_pickup() -> None:
    c = _off_route_city(-0.5, 0, qty=8)
    (r,) = c.routes
    p = c.params
    nodes = {n.id: n for n in c.nodes}
    depot, b, x = (nodes[node_id] for node_id in (r.depot_id, "B", "X"))
    extra_km = dist_km(b, x, p) + dist_km(x, depot, p) - dist_km(b, depot, p)
    extra_min = (
        travel_min(dist_km(b, x, p), p) + p.stop_service_min
        + travel_min(dist_km(x, depot, p), p) - travel_min(dist_km(b, depot, p), p)
    )
    limit = min(p.max_added_min_abs, p.max_added_share * route_duration_min(r, nodes, p))
    assert extra_min <= limit
    x_stop = Stop(
        node_id="X", eta_min=r.stops[-1].eta_min + travel_min(dist_km(b, x, p), p),
        service_min=p.stop_service_min, added=True,
    )
    alternative_route = r.model_copy(update={"stops": [*r.stops, x_stop]})
    alternative = c.model_copy(update={"routes": [alternative_route]})
    feasible, _ = nightly_plan(alternative)
    assert [(a.variant, a.qty) for a in feasible.assignments] == [(Variant.VAN, 8)]
    feasible_cost = feasible.total_money_kop + round(
        extra_km * p.van_kop_per_km + extra_min * p.van_kop_per_min
    )
    assert feasible_cost < feasible.hub_money_kop

    plan, _ = nightly_plan(c)
    assert plan.total_money_kop <= feasible_cost, (plan.total_money_kop, feasible_cost)
    assert [(a.variant, a.qty) for a in plan.assignments] == [(Variant.VAN, 8)]


def test_shared_capacity_prefers_cheapest_allocation_across_skus() -> None:
    routes = [route("R1", [("A", 60), ("B", 120)], 1), route("R2", [("T", 60), ("B", 125)], 2)]
    c = city(routes, [("A", 1), ("T", 2)], [Order(id="two", sku_id="S", pvz_id="B", qty=2)])
    c.skus.append(Sku(id="S2"))
    c.stock.append(StockItem(node_id="A", sku_id="S2", qty=1))
    c.orders.append(Order(id="one", sku_id="S2", pvz_id="B"))
    c.params.max_new_stops = 0

    # Первую партию можно взять целиком из T и сохранить единственное место R1 для S2.
    spare = c.model_copy(update={"stock": c.stock[1:]})
    feasible, _ = nightly_plan(spare)
    assert {(a.order_id, a.source_node, a.qty) for a in feasible.assignments} == {
        ("two", "T", 2), ("one", "A", 1)
    }
    plan, _ = nightly_plan(c)
    assert plan.total_money_kop <= feasible.total_money_kop, (
        plan.total_money_kop, feasible.total_money_kop
    )
    assert all(a.variant == Variant.VAN for a in plan.assignments)


def test_closing_only_source_of_unique_sku_reports_unavailable(demo_client: TestClient) -> None:
    response = demo_client.post(
        "/routing/demo/orders", headers={**HEADERS, "Idempotency-Key": "unique-order"},
        json={"id": "unique", "sku_id": "UNIQUE-ПВЗ-D", "pvz_id": "ПВЗ-F"},
    )
    assert response.status_code == 200
    assert response.json()["assignment"]["variant"] == Variant.VAN

    response = demo_client.post(
        "/routing/demo/incidents/ПВЗ-D", params={"now_min": 0},
        headers={**HEADERS, "Idempotency-Key": "close-source"},
    )
    assert response.status_code == 200, response.text
    (incident,) = response.json()
    assert incident["order_id"] == "unique"
    assert len(incident["notifications"]) == 2
    assert all("доступного маршрута нет" in text for text in incident["notifications"])
    assert (incident["extra_cost_kop"], incident["extra_hours"]) == (0, 0)
    assert not any(candidate["chosen"] for candidate in incident["candidates"])


def test_plan_rejects_duplicate_route_ids(demo_client: TestClient) -> None:
    routes = [route("R1", [("A", 60), ("B", 120)]), route("R2", [("C", 65), ("X", 125)])]
    c = city(routes, [("A", 1)], [Order(id="o", sku_id="S", pvz_id="X")])
    c.params.max_new_stops = 0
    payload = c.model_dump(mode="json")
    payload["routes"][1]["id"] = payload["routes"][0]["id"]

    response = demo_client.post("/routing/plan", headers=HEADERS, json=payload)
    assert response.status_code == 422, response.text
    assert "повторяются id маршрутов" in response.json()["detail"][0]["msg"]
