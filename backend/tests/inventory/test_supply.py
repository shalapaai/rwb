"""Поставка продавца (HB1): система раскладывает партию — полка ПВЗ, газель, хаб."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.modules.catalog.models import Category, Sale
from app.modules.economics.params import DEFAULTS as P
from app.modules.inventory.models import Product, Seller, Unit
from app.modules.inventory.service import sim_now
from app.modules.ledger.models import LedgerEntry
from app.modules.network.models import Node

SELLER = {"X-Role": "seller"}


@pytest.fixture
def city(db):
    """ПВЗ T-U (тут хит берут 0,5 шт./сут → нужно 7 шт.), закрытый T-V и хаб."""
    db.add_all([
        Node(id="T-U", kind="pvz", x_km=0, y_km=0, storage_total_l=100),
        Node(id="T-V", kind="pvz", x_km=1, y_km=0, storage_total_l=100, is_open=False),
        Seller(id="T-S8"),
        Category(id="T-SC", name="Тест поставка", mesh_allowed=True, marked=False),
        Category(id="T-SM", name="Тест поставка маркировка", mesh_allowed=False, marked=True,
                 ban_reason="Маркировка"),
    ])  # fmt: skip
    if db.get(Node, "HUB") is None:
        db.add(Node(id="HUB", kind="hub", x_km=0, y_km=0, storage_total_l=0))
    db.flush()
    base = {"seller_id": "T-S8", "volume_l": 1.0, "price_kop": 100}
    db.add_all([
        Product(sku="T-SH", category_id="T-SC", **base),
        Product(sku="T-SX", category_id="T-SM", mesh_allowed=False, **base),
    ])  # fmt: skip
    db.flush()
    today = sim_now(db).date()
    for d in range(0, 30, 2):
        db.add(Sale(sku="T-SH", pvz_id="T-U", day=today - timedelta(days=d), qty=1))
    db.flush()
    return "T-U"


def drop(client, node, sku, qty, key):
    r = client.post(
        f"/pvz/{node}/supply",
        headers={**SELLER, "Idempotency-Key": key},
        json={"sku_id": sku, "qty": qty},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_pvz_dropoff_keeps_what_point_needs_rest_to_hub(client, db, city):
    res = drop(client, "T-U", "T-SH", 10, "s1")
    split = {(p["to"], p["how"]): p["qty"] for p in res["plan"]}
    assert split[("T-U", "shelf")] == 7  # 0,5 шт./сут × 14 сут
    assert sum(split.values()) == 10
    units = db.scalars(select(Unit).where(Unit.sku == "T-SH")).all()
    assert sum(u.status == "on_shelf" and u.node_id == "T-U" for u in units) == 7
    assert {u.source for u in units} == {"seller"}
    entries = db.execute(
        select(func.sum(LedgerEntry.amount_kop)).where(LedgerEntry.txn_id.in_(res["txn_ids"]))
    ).scalar()
    assert entries == 0  # каждая проводка сходится в ноль
    assert drop(client, "T-U", "T-SH", 10, "s1") == res  # повтор не дублирует


def test_banned_goes_to_hub_whole(client, city):
    res = drop(client, "T-U", "T-SX", 4, "s2")
    assert [(p["how"], p["qty"]) for p in res["plan"]] == [("hub", 4)]
    assert "Маркировка" in res["plan"][0]["reason"]


def test_hub_dropoff_ships_to_point_that_needs_it(client, db, city):
    res = drop(client, "HUB", "T-SH", 9, "s3")
    split = {(p["to"], p["how"]): p["qty"] for p in res["plan"]}
    assert split[("T-U", "hub_van")] == 7
    assert sum(split.values()) == 9
    hub_cost = db.execute(
        select(func.sum(LedgerEntry.amount_kop)).where(
            LedgerEntry.txn_id.in_(res["txn_ids"]), LedgerEntry.account == "ext:hub_ops"
        )
    ).scalar()
    assert hub_cost == P.hub_order_kop * 7


def test_closed_pvz_rejects(client, city):
    r = client.post(
        "/pvz/T-V/supply",
        headers={**SELLER, "Idempotency-Key": "s4"},
        json={"sku_id": "T-SH", "qty": 1},
    )
    assert r.status_code == 409
