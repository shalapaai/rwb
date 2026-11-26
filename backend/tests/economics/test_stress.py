"""Проверки восьми стресс-сценариев, границ решения и бюджета ПВЗ.
Ожидаемые суммы для нулевого спроса и пустой полки считаются из исходных ставок.
Для границы учитываем знак до округления денежных статей до копеек.
"""

from dataclasses import replace
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app
from app.modules.economics.params import DEFAULTS
from app.modules.economics.pvz import pvz_month

client = TestClient(app, headers={"X-Role": "seller"})


def test_sensitivity_axes_and_zero_demand():
    axes = {
        "demand_per_day",
        "shelf_days",
        "volume_l",
        "opened_share",
        "repack_cost_kop",
        "storage_tariff_kop_l_day",
        "loss_share",
        "season_factor",
    }
    for name in axes:
        response = client.get("/model/sensitivity", params={"param": name, "steps": 3})
        assert response.status_code == 200
        points = response.json()["points"]
        assert len(points) == 3
        assert Decimal(points[0]["x"]) < Decimal(points[1]["x"]) < Decimal(points[2]["x"])
        for row in points:
            assert sum(row["distribution"].values()) == row["effect_kop"]

    # При λ=0 расходуются проверка, 1/4 переупаковки, 14 суток полки и резерв потерь.
    query = {"param": "demand_per_day", "from": 0, "to": 1, "steps": 2}
    first, last = client.get("/model/sensitivity", params=query).json()["points"]
    assert first["effect_kop"] == -2552  # −1650 − 1406/4 − 2×14×10 − 90000×0,003
    assert first["effect_after_storage_fee_kop"] == -2692
    assert last["effect_kop"] > 0

    prices = {"param": "storage_tariff_kop_l_day", "from": 10, "to": 20, "steps": 2}
    low, high = client.get("/model/sensitivity", params=prices).json()["points"]
    assert low["effect_kop"] == high["effect_kop"]
    assert high["effect_after_storage_fee_kop"] < low["effect_after_storage_fee_kop"]
    assert high["distribution"]["pvz_kop"] > low["distribution"]["pvz_kop"]


def test_boundaries_select_hub_at_zero_and_distinguish_internal_tariff():
    response = client.get("/model/boundary")
    assert response.status_code == 200
    bounds = {row["param"]: row for row in response.json()}
    assert len(bounds) == 8
    demand = bounds["demand_per_day"]["chain"]
    assert 0 < Decimal(demand["hub_edge"]) < Decimal(demand["mesh_edge"]) < 1
    assert Decimal(demand["mesh_edge"]) - Decimal(demand["hub_edge"]) == Decimal("0.000001")
    fine = {
        "param": "demand_per_day",
        "from": demand["hub_edge"],
        "to": demand["mesh_edge"],
        "steps": 2,
    }
    adjacent = client.get("/model/sensitivity", params=fine).json()["points"]
    assert [row["mesh_profitable"] for row in adjacent] == [False, True]
    # При сроке 0 суток продажа с полки невозможна; при базовом спросе одних суток достаточно.
    days = bounds["shelf_days"]["chain"]
    assert (days["hub_edge"], days["mesh_edge"]) == (0, 1)
    loss = bounds["loss_share"]["chain"]
    assert Decimal(loss["mesh_edge"]) < Decimal(loss["hub_edge"])
    tariff = bounds["storage_tariff_kop_l_day"]
    assert tariff["chain"]["threshold"] is None
    rule = tariff["after_storage_fee"]
    assert rule["mesh_edge"] + 1 == rule["hub_edge"]
    query = {
        "param": tariff["param"],
        "from": rule["mesh_edge"],
        "to": rule["hub_edge"],
        "steps": 2,
    }
    left, right = client.get("/model/sensitivity", params=query).json()["points"]
    assert left["effect_after_storage_fee_kop"] >= 0 >= right["effect_after_storage_fee_kop"]


def test_monthly_costs_and_break_even():
    for share in (10, 50):
        row = client.get("/model/pvz-economics", params={"share": share, "utilization": 0}).json()
        assert Decimal(row["shelf_l"]) == 2000 * share // 100
        assert row["storage_tariff_kop_l_day"] == 15
        assert row["income_kop"] == row["labour_cost_kop"] == row["materials_cost_kop"] == 0
        assert row["profit_kop"] == -row["space_cost_kop"] == -30 * 5 * Decimal(row["shelf_l"])
    row = client.get("/model/pvz-economics", params={"share": 25, "utilization": "0.5"}).json()
    assert Decimal(row["occupied_l"]) == 250
    assert row["storage_income_kop"] == 250 * 30 * 15
    assert row["income_kop"] == row["storage_income_kop"] + row["operations_income_kop"]
    assert (
        row["cost_kop"]
        == row["space_cost_kop"] + row["materials_cost_kop"] + row["labour_cost_kop"]
    )
    assert row["profit_kop"] == row["income_kop"] - row["cost_kop"]
    assert 0 < Decimal(row["break_even_utilization"]) < Decimal("0.5")
    assert row["profit_kop"] == 278984
    full = client.get("/model/pvz-economics", params={"utilization": 1}).json()
    assert full["profit_kop"] == 632967

    # При равенстве выплат и затрат на каждую операцию только хранение даёт прибыль.
    p = replace(DEFAULTS, opened_share=Decimal(0), wage_kop_hour=0)
    balanced = pvz_month(25, Decimal("0.5"), p)
    assert balanced.break_even_utilization == Decimal("0.5")
    assert balanced.profit_kop == 0


def test_invalid_ranges():
    for query in (
        {"param": "loss_share", "to": 2},
        {"param": "shelf_days", "to": "2.5"},
        {"from": 3, "to": 1},
        {"param": "volume_l", "from": 0},
        {"param": "repack_cost_kop", "from": 10, "to": 11, "steps": 3},
    ):
        assert client.get("/model/sensitivity", params=query).status_code == 422
    for query in ({"share": 9}, {"share": 51}, {"utilization": "1.01"}):
        assert client.get("/model/pvz-economics", params=query).status_code == 422
