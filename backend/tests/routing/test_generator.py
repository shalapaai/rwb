"""Проверки генератора города и отчёта устойчивости M5.

Сверяем порядок остановок, интерполяцию денег и сравнение на общих заказах.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.economics.params import kopecks, routing_params
from app.modules.routing.compare import compare
from app.modules.routing.generator import _percentiles, generate_city
from app.modules.routing.models import NodeKind
from app.modules.routing.network import dist_km, travel_min


def test_seed_reproduces_all_city_inputs() -> None:
    assert generate_city(7).model_dump() == generate_city(7).model_dump()
    assert generate_city(7).nodes != generate_city(8).nodes


@pytest.mark.parametrize("seed", [0, 1, 14, 19])
def test_city_geometry_and_routes(seed: int) -> None:
    city = generate_city(seed)
    assert city.params == routing_params()
    pvzs = {n.id: n for n in city.nodes if n.kind == NodeKind.PVZ}
    hub = next(n for n in city.nodes if n.kind == NodeKind.HUB)
    assert 20 <= len(pvzs) <= 40
    assert all(0 <= node.x_km <= 15 and 0 <= node.y_km <= 15 for node in pvzs.values())
    assert not (0 <= hub.x_km <= 15 and 0 <= hub.y_km <= 15)
    assert city.routes and city.orders and city.forecast and city.stock
    assert all(item.node_id in pvzs and item.qty > 0 for item in city.stock)
    for route in city.routes:
        assert route.depot_id == hub.id
        assert 5 <= len(route.stops) <= 15
        assert sum(stop.base_drop for stop in route.stops) <= route.capacity
        unvisited = dict(pvzs)
        previous = hub
        departure = 0
        for index, stop in enumerate(route.stops):
            if index > 0:
                expected = min(
                    unvisited,
                    key=lambda node_id: (
                        dist_km(previous, unvisited[node_id], city.params),
                        node_id,
                    ),
                )
                assert stop.node_id == expected
            node = unvisited.pop(stop.node_id)
            assert stop.eta_min == departure + travel_min(
                dist_km(previous, node, city.params), city.params
            )
            previous, departure = node, stop.eta_min + stop.service_min


def test_money_percentiles_interpolate_and_keep_integer_kopecks() -> None:
    interval = _percentiles([106, 101], kopecks)
    assert interval.model_dump() == {"median": 104, "p10": 102, "p90": 106}
    assert all(type(value) is int for value in interval.model_dump().values())


def test_robustness_api_runs_fixed_twenty_seeds_and_reports_cost_gaps() -> None:
    with TestClient(app) as client:
        assert client.get("/model/robustness").status_code == 401
        response = client.get("/model/robustness", headers={"X-Role": "logist"})
    assert response.status_code == 200
    report = response.json()
    seeds = list(range(20))
    assert report["seeds"] == [row["seed"] for row in report["runs"]] == seeds
    assert [row["scheme"] for row in report["schemes"]] == [
        "Всё через хаб",
        "По одному заказу (жадно)",
        "Ночной план",
    ]
    assert report["runs"][0]["schemes"] == [row.model_dump() for row in compare(generate_city(0))]
    params = routing_params()
    for row in report["runs"]:
        hub = row["schemes"][0]
        assert hub["money_kop"] == params.hub_kop * row["order_units"]
        assert hub["avg_hours"] == params.hub_hours
        assert hub["hub_units"] == row["order_units"]
    for scheme in report["schemes"]:
        for metric in ("money_per_unit_kop", "avg_hours", "local_share"):
            values = scheme[metric]
            assert values["p10"] <= values["median"] <= values["p90"]
        assert 0 <= scheme["local_share"]["p10"] <= scheme["local_share"]["p90"] <= 1
        assert all(type(value) is int for value in scheme["money_per_unit_kop"].values())
    expected_gaps = {
        row["seed"]: row["schemes"][2]["money_kop"] - row["schemes"][1]["money_kop"]
        for row in report["runs"]
        if row["schemes"][2]["money_kop"] >= row["schemes"][1]["money_kop"]
    }
    assert expected_gaps
    assert {
        row["seed"]: row["delta_kop"] for row in report["not_better_than_greedy"]
    } == expected_gaps
    for row in report["not_better_than_greedy"]:
        assert row["order_cost_delta_kop"] + row["detour_cost_kop"] == row["delta_kop"]
        assert row["reason"]
