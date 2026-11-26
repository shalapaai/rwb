"""Пополнение полки из хаба (HB1): популярное в этой точке — на полку, проводки, идемпотентность."""

import math
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.modules.catalog.models import Category, Sale
from app.modules.economics.params import DEFAULTS as P
from app.modules.economics.params import kopecks
from app.modules.inventory.models import Product, Seller, Unit
from app.modules.inventory.service import sim_now
from app.modules.ledger.models import LedgerEntry
from app.modules.network.models import Node

OPS = {"X-Role": "logist"}


@pytest.fixture
def point(db):
    """ПВЗ T-R (полка 25 л) и соседний T-Q. В T-R хит продаётся 1 шт./сут, второй — 0,5."""
    db.add_all([
        Node(id="T-R", kind="pvz", x_km=0, y_km=0, storage_total_l=100),
        Node(id="T-Q", kind="pvz", x_km=1, y_km=0, storage_total_l=100),
        Seller(id="T-S7"),
        Category(id="T-HB", name="Тест HB1", mesh_allowed=True, marked=False),
        Category(id="T-HM", name="Тест HB1 маркировка", mesh_allowed=False, marked=True,
                 ban_reason="Маркировка"),
    ])  # fmt: skip
    db.flush()
    base = {"seller_id": "T-S7", "volume_l": 1.0, "price_kop": 100}
    db.add_all([
        Product(sku="T-H1", category_id="T-HB", **base),
        Product(sku="T-H2", category_id="T-HB", **base),
        Product(sku="T-FAR", category_id="T-HB", **base),  # хит, но только в соседнем ПВЗ
        Product(sku="T-MRK", category_id="T-HM", mesh_allowed=False, **base),
    ])  # fmt: skip
    db.flush()
    today = sim_now(db).date()
    for d in range(30):
        day = today - timedelta(days=d)
        db.add(Sale(sku="T-H1", pvz_id="T-R", day=day, qty=1))
        db.add(Sale(sku="T-MRK", pvz_id="T-R", day=day, qty=2))
        db.add(Sale(sku="T-FAR", pvz_id="T-Q", day=day, qty=3))
        if d % 2 == 0:
            db.add(Sale(sku="T-H2", pvz_id="T-R", day=day, qty=1))
    db.flush()
    return "T-R"


def test_plan_takes_point_hits_for_shelf_period(client, point):
    plan = client.get("/pvz/T-R/restock-plan", headers=OPS).json()
    got = {i["sku"]: i["qty"] for i in plan["items"]}
    # спрос точки × 14 сут: 1 × 14 = 14 и 0,5 × 14 = 7; в соседнем ПВЗ хит сюда не везём
    assert got == {"T-H1": math.ceil(1 * P.shelf_days), "T-H2": math.ceil(0.5 * P.shelf_days)}
    assert any(s["sku"] == "T-MRK" and "Маркировка" in s["reason"] for s in plan["skipped"])


def test_restock_puts_units_on_shelf_and_posts_costs(client, db, point):
    r = client.post("/pvz/T-R/restock", headers={**OPS, "Idempotency-Key": "hb1"})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["delivered"] == 21 and res["used_l"] == 21
    units = db.scalars(select(Unit).where(Unit.node_id == "T-R")).all()
    assert {(u.source, u.status) for u in units} == {("hub", "on_shelf")}
    legs = dict(
        db.execute(
            select(LedgerEntry.account, func.sum(LedgerEntry.amount_kop))
            .where(LedgerEntry.txn_id.in_(res["txn_ids"]))
            .group_by(LedgerEntry.account)
        ).all()
    )
    work = kopecks(P.labour(P.receive_min)) * 21
    assert legs["ext:hub_ops"] == P.hub_order_kop * 21
    assert legs["pvz:T-R"] == P.receive_fee_kop * 21 - work
    assert sum(legs.values()) == 0
    # повтор с тем же ключом — тот же ответ, новых единиц нет
    again = client.post("/pvz/T-R/restock", headers={**OPS, "Idempotency-Key": "hb1"}).json()
    assert again == res
    # новый ключ: всё уже лежит — план пуст
    more = client.post("/pvz/T-R/restock", headers={**OPS, "Idempotency-Key": "hb1-2"}).json()
    assert more["delivered"] == 0


def test_shelf_space_limits_restock(client, db, point):
    db.get(Node, "T-R").storage_total_l = 40  # 25% → 10 л на полке
    db.flush()
    plan = client.get("/pvz/T-R/restock-plan", headers=OPS).json()
    assert sum(i["qty"] for i in plan["items"]) == 10


def test_closed_point_and_roles(client, db, point):
    assert client.post("/pvz/T-R/restock", headers={"X-Role": "seller", "Idempotency-Key": "x"}
                       ).status_code == 403  # fmt: skip
    db.get(Node, "T-R").is_open = False
    db.flush()
    r = client.post("/pvz/T-R/restock", headers={**OPS, "Idempotency-Key": "closed"})
    assert r.status_code == 409
