"""Проверки модели пригодного отказа через публичный API.

Нулевой спрос даёт точную стоимость базового пути через хаб.
Сценарии B проверяют, как внутренняя оплата меняет выгоду сторон.
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.economics.params import routing_params

client = TestClient(app)
HEADERS = {"X-Role": "seller"}
MODEL = "/model/unit-economics"


def test_catalog_and_query_contract():
    assert client.get("/model/params").status_code == 401
    assert client.get(MODEL).status_code == 401
    response = client.get("/model/params", headers=HEADERS)
    assert response.status_code == 200
    params = response.json()
    assert params
    assert len({row["id"] for row in params}) == len(params)
    for row in params:
        assert row["label"] and row["unit"] and row["basis"]
        assert row["value"] is not None
    for query in ({"grade": "C"}, {"demand_per_day": "-0.1"}, {"volume_l": "0"}):
        assert client.get(MODEL, headers=HEADERS, params=query).status_code == 422


def test_no_demand_charges_full_shelf_period_then_hub():
    query = {"grade": "A", "volume_l": 2, "demand_per_day": 0, "shelf_days": 7}
    response = client.get(MODEL, params=query, headers=HEADERS)
    assert response.status_code == 200
    result = response.json()
    assert Decimal(str(result["sale_probability"])) == 0
    assert Decimal(str(result["expected_days"])) == 7
    assert result["base"]["total_kop"] == 3700 + 6040
    # 3 минуты по 330 ₽/ч и 2 л × 7 суток по 10 коп. реальных затрат.
    assert result["mesh"]["total_kop"] == 9740 + 1650 + 140
    assert result["storage_payment_kop"] == 2 * 7 * 15
    assert result["effect_after_storage_fee_kop"] == -1650 - 2 * 7 * 15
    assert result["distribution"] == {"pvz_kop": -80, "seller_kop": 0, "mp_kop": -1710}
    assert result["effect_kop"] == result["base"]["total_kop"] - result["mesh"]["total_kop"]


def test_repacking_payment_preserves_chain_effect_to_kopeck():
    query = {"grade": "B", "volume_l": "2.3", "demand_per_day": "0.1", "shelf_days": 10}
    results = {}
    for payer in ("mp", "seller"):
        response = client.get(MODEL, params={**query, "repack_payer": payer}, headers=HEADERS)
        assert response.status_code == 200
        result = response.json()
        results[payer] = result
        assert float(result["sale_probability"]) == pytest.approx(0.63212056)
        assert float(result["expected_days"]) == pytest.approx(6.3212056)
        for scheme in (result["base"], result["mesh"]):
            costs = [row["amount_kop"] for row in scheme["items"]]
            assert costs and all(type(value) is int for value in costs)
            assert sum(costs) == scheme["total_kop"]
        shares = result["distribution"]
        assert shares["pvz_kop"] + shares["seller_kop"] + shares["mp_kop"] == result["effect_kop"]
        assert result["effect_kop"] == result["base"]["total_kop"] - result["mesh"]["total_kop"]
    assert results["mp"]["effect_kop"] == results["seller"]["effect_kop"]
    mp, seller = (results[payer]["distribution"] for payer in ("mp", "seller"))
    assert mp["pvz_kop"] == seller["pvz_kop"]
    assert mp["seller_kop"] - seller["seller_kop"] == seller["mp_kop"] - mp["mp_kop"] > 0


def test_team_defaults_and_routing_prices():
    result = client.get(MODEL, headers=HEADERS, params={"grade": "B", "demand_per_day": 0}).json()
    assert (result["shelf_days"], result["repack_payer"]) == (14, "mp")
    # Пакет, две минуты на упаковку, 14 суток хранения и полный маршрут через хаб.
    assert result["mesh"]["total_kop"] == 9740 + 1650 + 306 + 1100 + 280
    assert Decimal(result["local_delivery_min"]) == pytest.approx(Decimal(3 * 30 + 14 * 720) / 17)
    assert result["hub_delivery_min"] == 2160
    routing = routing_params()
    assert (routing.hub_kop, routing.own_kop, routing.move_kop) == (6040, 925, 813)
