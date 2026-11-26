"""Приём отказа в ПВЗ (N4): оценка A/B/C, лимит полки, идемпотентность, начисления."""

import pytest
from sqlalchemy import func, select

from app.modules.inventory.models import Product, Return, Seller, Unit
from app.modules.ledger.models import LedgerEntry, LedgerTxn
from app.modules.ledger.service import unbalanced_txn_ids
from app.modules.network.models import Node
from app.modules.ops.models import SimClock

OP = {"X-Role": "pvz_operator"}


@pytest.fixture
def pvz(db):
    """ПВЗ со складом 16 л: при доле 25% под mesh — 4 л, то есть две единицы по 2 л."""
    db.add_all([
        Node(id="T-A", kind="pvz", x_km=0, y_km=0, storage_total_l=16),
        Node(id="T-Z", kind="pvz", x_km=1, y_km=0, storage_total_l=16, is_open=False),
        Seller(id="T-S1"),
    ])  # fmt: skip
    db.flush()
    db.add_all([
        Product(sku="T-CUP", seller_id="T-S1", volume_l=2.0, price_kop=150000),
        Product(sku="T-SOFA", seller_id="T-S1", volume_l=2.0, price_kop=1, mesh_allowed=False),
    ])  # fmt: skip
    db.flush()
    return "T-A"


def refuse(client, grade, key, sku="T-CUP", pvz="T-A", **extra):
    return client.post(
        f"/pvz/{pvz}/refusals",
        headers={**OP, "Idempotency-Key": key},
        json={"sku_id": sku, "grade": grade, **extra},
    )


def legs(db, txn_ids):
    rows = db.execute(
        select(LedgerEntry.account, LedgerEntry.amount_kop).where(LedgerEntry.txn_id.in_(txn_ids))
    ).all()
    return sorted(rows)


def test_grade_a_goes_to_shelf_and_pays_check(client, db, pvz):
    r = refuse(client, "A", "k-a").json()
    assert r["placement"] == "shelf" and r["decision"] == "shelf"
    assert r["used_l"] == 2.0 and r["limit_l"] == 4.0
    unit = db.get(Unit, r["unit_id"])
    assert unit.status == "on_shelf" and unit.grade == "A" and unit.shelved_at is not None
    # МП платит ПВЗ 15 ₽ за проверку, ПВЗ тратит 3 мин сотрудника = 12,50 ₽
    assert legs(db, r["txn_ids"]) == sorted(
        [("mp", -1500), ("pvz:T-A", 1500), ("pvz:T-A", -1250), ("ext:labour", 1250)]
    )
    assert unbalanced_txn_ids(db) == []


def test_grade_b_is_repacked_and_pays_both(client, db, pvz):
    r = refuse(client, "B", "k-b").json()
    assert r["placement"] == "shelf" and r["decision"] == "repack"
    assert len(r["txn_ids"]) == 4  # проверка и переупаковка: тариф + труд каждая
    pvz_net = sum(a for acc, a in legs(db, r["txn_ids"]) if acc == "pvz:T-A")
    assert pvz_net == 2 * (1500 - 1250)  # ПВЗ зарабатывает по 2,50 ₽ на каждой операции


def test_grade_c_goes_to_hub_but_check_is_paid(client, db, pvz):
    r = refuse(client, "C", "k-c").json()
    assert r["placement"] == "hub" and "оценка C" in r["reason"]
    assert db.get(Unit, r["unit_id"]).status == "to_hub"
    assert len(r["txn_ids"]) == 2  # проверку оплачиваем при любой оценке
    assert r["used_l"] == 0.0


def test_full_shelf_sends_to_hub_with_reason(client, db, pvz):
    assert refuse(client, "A", "f1").json()["placement"] == "shelf"
    assert refuse(client, "A", "f2").json()["placement"] == "shelf"
    third = refuse(client, "A", "f3").json()
    assert third["placement"] == "hub"
    assert "полка заполнена" in third["reason"] and "4 из 4 л" in third["reason"]
    ret = db.scalar(select(Return).where(Return.unit_id == third["unit_id"]))
    assert ret.kind == "refusal" and ret.decision == "hub"


def test_same_key_returns_same_answer(client, db, pvz):
    first = refuse(client, "A", "dup").json()
    again = refuse(client, "A", "dup").json()
    assert again == first
    assert db.scalar(select(func.count()).select_from(Unit).where(Unit.sku == "T-CUP")) == 1
    n_txn = db.scalar(
        select(func.count()).select_from(LedgerTxn).where(LedgerTxn.id.in_(first["txn_ids"]))
    )
    assert n_txn == 2


def test_not_allowed_product_goes_to_hub(client, pvz):
    r = refuse(client, "A", "sofa", sku="T-SOFA").json()
    assert r["placement"] == "hub" and "не допущен" in r["reason"]


@pytest.mark.parametrize(
    ("kwargs", "headers", "status"),
    [
        ({"pvz": "T-Z"}, OP, 409),  # ПВЗ закрыт
        ({"pvz": "NOPE"}, OP, 404),
        ({"sku": "NOPE"}, OP, 404),
        ({"order_id": 999999}, OP, 404),
        ({}, {"X-Role": "seller"}, 403),  # отказы принимает только оператор ПВЗ
    ],
)
def test_rejections(client, pvz, kwargs, headers, status):
    sku = kwargs.pop("sku", "T-CUP")
    target = kwargs.pop("pvz", "T-A")
    r = client.post(
        f"/pvz/{target}/refusals",
        headers={**headers, "Idempotency-Key": "x"},
        json={"sku_id": sku, "grade": "A", **kwargs},
    )
    assert r.status_code == status


def test_key_header_is_required(client, pvz):
    r = client.post("/pvz/T-A/refusals", headers=OP, json={"sku_id": "T-CUP", "grade": "A"})
    assert r.status_code == 422


def test_settings_change_limit(client, db, pvz):
    h = {**OP, "Idempotency-Key": "s1"}
    assert (
        client.patch("/pvz/T-A/settings", headers=h, json={"mesh_share_pct": 60}).status_code == 422
    )
    r = client.patch("/pvz/T-A/settings", headers=h, json={"mesh_share_pct": 50}).json()
    assert r["limit_l"] == 8.0 and r["over_limit"] is False
    for i in range(4):  # теперь влезают четыре единицы
        assert refuse(client, "A", f"s-{i}").json()["placement"] == "shelf"
    down = client.patch(
        "/pvz/T-A/settings", headers={**OP, "Idempotency-Key": "s2"}, json={"mesh_share_pct": 10}
    ).json()
    assert down["over_limit"] is True  # уже лежащее не выбрасываем, новые отказы уйдут в хаб
    assert refuse(client, "A", "s-5").json()["placement"] == "hub"


def test_stock_is_fifo_and_shows_age(client, db, pvz):
    old = refuse(client, "A", "old").json()["unit_id"]
    db.merge(SimClock(id=1, offset_days=5))  # прошло 5 дней
    db.flush()
    new = refuse(client, "B", "new").json()["unit_id"]
    s = client.get("/pvz/T-A/stock", headers={"X-Role": "seller"}).json()
    assert [u["unit_id"] for u in s["units"]] == [old, new]  # старые сверху
    assert [u["age_days"] for u in s["units"]] == [5, 0]
    assert s["used_l"] == 4.0 and s["limit_l"] == 4.0
