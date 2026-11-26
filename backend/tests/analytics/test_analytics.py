"""Заглушка хаба и аналитика (M2): числа берутся из журнала и сходятся до копейки."""

from uuid import uuid4

import pytest

from app.modules.economics.params import DEFAULTS as P
from app.modules.orders import pricing
from app.seed import seed

ADMIN, OP = {"X-Role": "admin"}, {"X-Role": "pvz_operator"}
A, B = "ПВЗ-A", "ПВЗ-B"


def post(client, path, headers, body):
    r = client.post(path, headers={**headers, "Idempotency-Key": uuid4().hex}, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def deliver(client, pvz, sku="SKU-001"):
    """Заказ проходит все шаги до выдачи."""
    o = post(client, "/orders", ADMIN, {"sku_id": sku, "pvz_id": pvz})
    steps = {
        "van": ["pick", "load", "receive", "issue"],
        "own": ["pick", "issue"],
        "hub": ["receive", "issue"],
    }[o["variant"]]
    for a in steps:
        o = post(client, f"/orders/{o['id']}/{a}", OP, {"version": o["version"]})
    assert o["status"] == "issued"
    return o


@pytest.fixture
def city(db, client):
    seed(db)
    db.flush()
    post(client, f"/pvz/{A}/refusals", OP, {"sku_id": "SKU-001", "grade": "B"})
    return {"van": deliver(client, B), "hub": deliver(client, B, sku="SKU-002")}


def test_stub_hub_is_marked_imitation(client, city):
    q = client.get("/stub/hub", headers=ADMIN).json()
    assert q["imitation"] and q["label"] == "ИМИТАЦИЯ"
    assert (q["price_kop"], q["hours"]) == (P.hub_order_kop, P.hub_delivery_min / 60)
    assert all(s["imitation"] for s in q["segments"])
    ship = client.get(f"/stub/hub/orders/{city['hub']['id']}", headers=ADMIN).json()
    assert ship["imitation"] and ship["pvz_id"] == B
    assert client.get(f"/stub/hub/orders/{city['van']['id']}", headers=ADMIN).status_code == 409


def test_chain_check_holds(client, city):
    c = client.get("/analytics/chain", headers=ADMIN).json()
    assert c["orders"] == 2
    assert c["check"]["ok"], c["check"]
    assert sum(c["parties_kop"].values()) == c["effect_kop"]
    assert c["effect_kop"] == c["ext_costs_base_kop"] - c["ext_costs_mesh_kop"]
    assert c["buyer_paid_mesh_kop"] == c["buyer_paid_base_kop"] == 2 * 150000
    by = {r["order_id"]: r for r in c["per_order"]}
    assert by[city["hub"]["id"]]["saved_kop"] == 0  # без отказа в городе выгоды нет
    assert by[city["van"]["id"]]["saved_kop"] > 0
    assert c["parties_kop"]["seller"] == P.seller_saving_kop  # продавцу — скидка на логистику
    # проверка и переупаковка отказа видны отдельной строкой, не прячутся
    assert c["refusal_handling_kop"]["ext_costs"] > 0


def test_chain_for_one_order(client, city):
    c = client.get(f"/analytics/chain?order_id={city['van']['id']}", headers=ADMIN).json()
    assert c["orders"] == 1 and c["check"]["ok"]
    assert c["effect_kop"] == c["per_order"][0]["saved_kop"]


def test_pvz_analytics(client, city):
    a = client.get(f"/analytics/pvz/{A}", headers=OP).json()
    ops = {i["op"]: i["amount_kop"] for i in a["income"]}
    assert ops["check"] == P.inspection_fee_kop and ops["repack"] == P.repack_fee_kop
    assert ops["pick"] == P.pick_fee_kop and ops["storage"] > 0
    assert {c["resource"] for c in a["costs"]} >= {"labour", "materials", "rent"}
    assert a["profit_kop"] == a["income_total_kop"] - a["costs_total_kop"] - a["penalties_kop"]
    assert (a["profit_kop"], a["profit_base_kop"]) == (825, 0)
    assert a["ops_today"] == 3 and a["ops_limit"] == 60  # проверка, переупаковка, отбор
    assert a["shelf"]["limit_l"] > 0

    b = client.get(f"/analytics/pvz/{B}", headers=OP).json()
    ops_b = {i["op"]: i["amount_kop"] for i in b["income"]}
    assert ops_b["receive"] == P.receive_fee_kop
    assert ops_b["issue_fee"] == 2 * pricing.pct(150000, pricing.ISSUE_FEE_PCT)  # два заказа
    assert (b["profit_kop"], b["profit_base_kop"]) == (9082, 9000)
    assert b["profit_base_kop"] == ops_b["issue_fee"]  # через хаб ПВЗ получает только за выдачу


def test_seller_analytics(client, city):
    s = client.get("/analytics/seller/S-01", headers={"X-Role": "seller"}).json()
    rows = {r["order_id"]: r for r in s["orders"]}
    van = rows[city["van"]["id"]]
    assert van["mesh"]["logistics_kop"] == pricing.SELLER_LOGISTICS_MESH_KOP
    assert van["base"]["logistics_kop"] == pricing.SELLER_LOGISTICS_BASE_KOP
    assert van["gain_kop"] == P.seller_saving_kop
    assert rows[city["hub"]["id"]]["gain_kop"] == 0
    assert s["summary"]["resold_locally"] == 1 and s["summary"]["issued"] == 2
    assert s["summary"]["refusals_on_shelves"] == len(s["refusals_on_shelves"]) > 0


def test_roles(client, city):
    assert client.get("/analytics/seller/S-01", headers=OP).status_code == 403
    assert client.get(f"/analytics/pvz/{A}", headers={"X-Role": "seller"}).status_code == 403
