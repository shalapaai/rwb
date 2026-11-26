"""Приём отказа с правилом каталога (MX2): непопулярный или запрещённый товар — в хаб."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.modules.catalog.models import Category, Sale
from app.modules.inventory.models import Product, Return, Seller, Unit
from app.modules.inventory.service import sim_now
from app.modules.network.models import Node

OP = {"X-Role": "pvz_operator"}


@pytest.fixture
def city(db):
    """ПВЗ T-M (полка 25 л) и три товара: хит, товар без продаж и маркированная обувь."""
    db.add_all([
        Node(id="T-M", kind="pvz", x_km=0, y_km=0, storage_total_l=100),
        Seller(id="T-S9"),
        Category(id="T-OK", name="Тест MX2", mesh_allowed=True, marked=False),
        Category(id="T-MK", name="Тест MX2 обувь", mesh_allowed=False, marked=True,
                 ban_reason="Маркировка «Честный знак»"),
    ])  # fmt: skip
    db.flush()
    base = {"seller_id": "T-S9", "volume_l": 1.0, "price_kop": 100}
    db.add_all([
        Product(sku="T-HIT", category_id="T-OK", **base),
        Product(sku="T-DUD", category_id="T-OK", **base),
        Product(sku="T-SHOE", category_id="T-MK", mesh_allowed=False, **base),
    ])  # fmt: skip
    db.flush()
    today = sim_now(db).date()
    for d in range(30):
        day = today - timedelta(days=d)
        db.add(Sale(sku="T-HIT", pvz_id="T-M", day=day, qty=2))  # 2 шт. в сутки
        db.add(Sale(sku="T-SHOE", pvz_id="T-M", day=day, qty=5))  # хит, но маркирован
    db.flush()
    return "T-M"


def refuse(client, sku, key):
    r = client.post(
        "/pvz/T-M/refusals",
        headers={**OP, "Idempotency-Key": key},
        json={"sku_id": sku, "grade": "A"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_hit_goes_to_shelf(client, city):
    r = refuse(client, "T-HIT", "mx2-hit")
    assert r["placement"] == "shelf" and r["decision"] == "shelf"


def test_unpopular_goes_to_hub_with_threshold(client, db, city):
    r = refuse(client, "T-DUD", "mx2-dud")
    assert (r["placement"], r["decision"]) == ("hub", "hub")
    assert "спрос 0/сут ниже порога" in r["reason"]
    unit = db.get(Unit, r["unit_id"])
    assert unit.status == "to_hub" and unit.shelved_at is None
    ret = db.scalar(select(Return).where(Return.unit_id == unit.id))
    assert ret.decision == "hub" and "ниже порога" in ret.reason


def test_marked_hit_still_goes_to_hub(client, city):
    r = refuse(client, "T-SHOE", "mx2-shoe")
    assert r["placement"] == "hub" and "Маркировка" in r["reason"]


def test_check_is_paid_even_when_rule_sends_to_hub(client, city):
    """Сотрудник всё равно осмотрел товар — проверку МП оплачивает, как при оценке C."""
    r = refuse(client, "T-DUD", "mx2-paid")
    assert len(r["txn_ids"]) == 2  # тариф проверки и реальная затрата труда


def test_repeat_returns_same_answer(client, city):
    assert refuse(client, "T-DUD", "mx2-same") == refuse(client, "T-DUD", "mx2-same")
