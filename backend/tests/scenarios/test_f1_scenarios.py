"""Сценарии 3–5 (F1): возврат после выдачи, закрытие ПВЗ в пути, залежавшийся отказ."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.modules.economics.params import DEFAULTS as P
from app.modules.economics.params import routing_params
from app.modules.inventory.models import Return, Unit
from app.modules.ledger.models import LedgerEntry, LedgerTxn
from app.modules.ledger.service import unbalanced_txn_ids
from app.modules.ops.models import Notification
from app.seed import seed

ADMIN, OP, LOGIST = {"X-Role": "admin"}, {"X-Role": "pvz_operator"}, {"X-Role": "logist"}
A, B, C = "ПВЗ-A", "ПВЗ-B", "ПВЗ-C"


def call(client, path, headers, body, expect=200):
    r = client.post(path, headers={**headers, "Idempotency-Key": uuid4().hex}, json=body)
    assert r.status_code == expect, r.text
    return r.json()


def step(client, o, action):
    return call(client, f"/orders/{o['id']}/{action}", OP, {"version": o["version"]})


def van_order(client, steps=("pick", "load")):
    o = call(client, "/orders", ADMIN, {"sku_id": "SKU-001", "pvz_id": B})
    assert o["variant"] == "van" and o["source_node"] == A  # газель G1: A → C → B
    for a in steps:
        o = step(client, o, a)
    return o


def mesh_sum(db, order_id, account):
    q = (
        select(func.coalesce(func.sum(LedgerEntry.amount_kop), 0))
        .join(LedgerTxn, LedgerTxn.id == LedgerEntry.txn_id)
        .where(LedgerTxn.order_id == order_id, LedgerTxn.scheme == "mesh")
        .where(LedgerEntry.account == account)
    )
    return db.scalar(q)


@pytest.fixture
def city(db):
    seed(db)
    db.flush()


# ---------------------------------------------------------------- сценарий 3


@pytest.mark.parametrize("grade", ["A", "C"])
def test_return_after_purchase_goes_to_hub(client, db, city, grade):
    o = van_order(client, ("pick", "load", "receive", "issue"))
    shelf_b = db.scalar(select(func.count()).where(Unit.node_id == B, Unit.status == "on_shelf"))
    r = call(client, f"/orders/{o['id']}/return", OP, {"version": o["version"], "grade": grade})
    assert r["status"] == "returned"
    unit = db.scalar(
        select(Unit)
        .join(Return, Return.unit_id == Unit.id)
        .where(Return.order_id == o["id"], Return.kind == "return")
    )
    assert (unit.status, unit.node_id) == ("to_hub", B)  # даже при оценке A — в хаб (N0)
    ret = db.scalar(select(Return).where(Return.order_id == o["id"], Return.kind == "return"))
    assert (ret.grade, ret.decision) == (grade, "hub")
    assert (
        db.scalar(select(func.count()).where(Unit.node_id == B, Unit.status == "on_shelf"))
        == shelf_b
    )  # доступный остаток B не вырос

    # деньги: оплата покупателю вернулась, выплата продавцу снята новыми проводками
    assert mesh_sum(db, o["id"], "ext:buyer") == 0
    assert mesh_sum(db, o["id"], "seller:S-01") == 0
    assert mesh_sum(db, o["id"], "ext:hub_ops") == P.hub_return_kop  # обратная дорога в хаб
    reversals = db.scalar(
        select(func.count()).where(LedgerTxn.order_id == o["id"], LedgerTxn.kind == "return")
    )
    assert reversals == 2  # оплата и выплата; исходные проводки выдачи на месте
    assert unbalanced_txn_ids(db) == []


def test_return_only_after_issue(client, city):
    o = van_order(client, ("pick",))
    call(
        client, f"/orders/{o['id']}/return", OP, {"version": o["version"], "grade": "A"}, expect=409
    )


# ---------------------------------------------------------------- сценарий 4


def test_pvz_closed_buyer_agrees_unload_at_c(client, db, city):
    o = van_order(client)
    r = call(client, "/incidents", LOGIST, {"node_id": B, "buyer_accepts_alt": True})
    [d] = r["rerouted"]
    assert d["order_id"] == o["id"] and d["status"] == "rerouted" and d["pvz_id"] == C
    assert d["penalty_kop"] == 2000 and r["penalty_kop"] == 2000
    assert mesh_sum(db, o["id"], f"pvz:{B}") <= -2000  # штраф платит закрывшийся ПВЗ
    texts = db.scalars(select(Notification.role).where(Notification.order_id == o["id"])).all()
    assert sorted(texts) == ["buyer", "seller"]

    # оператор C видит приём, принимает и выдаёт
    tasks = client.get(f"/pvz/{C}/tasks", headers=OP).json()
    assert [t["order_id"] for t in tasks["to_receive"]] == [o["id"]]
    o = client.get(f"/orders/{o['id']}", headers=OP).json()
    o = step(client, o, "receive")
    o = step(client, o, "issue")
    assert o["status"] == "issued"
    assert db.scalar(select(func.count()).where(Unit.node_id == C, Unit.status == "issued")) == 1
    assert unbalanced_txn_ids(db) == []

    # B закрыт: новых заказов туда не принимаем
    call(client, "/orders", ADMIN, {"sku_id": "SKU-001", "pvz_id": B}, expect=409)


def test_pvz_closed_buyer_refuses_next_day_van(client, db, city):
    o = van_order(client)
    r = call(client, "/incidents", LOGIST, {"node_id": B, "buyer_accepts_alt": False})
    [d] = r["rerouted"]
    assert d["pvz_id"] == B and d["extra_hours"] == 24
    assert d["extra_cost_kop"] == routing_params().move_kop  # завтрашний рейс
    assert mesh_sum(db, o["id"], "ext:van") >= d["extra_cost_kop"]
    assert unbalanced_txn_ids(db) == []
    # повторно закрыть уже закрытый ПВЗ нельзя
    call(client, "/incidents", LOGIST, {"node_id": B}, expect=409)


# ---------------------------------------------------------------- сценарий 5


def test_refusal_without_demand_notified_on_7_and_sent_to_hub_on_14(client, db, city):
    r = call(client, f"/pvz/{A}/refusals", OP, {"sku_id": "SKU-001", "grade": "A"})
    unit_id = r["unit_id"]

    week = call(client, "/sim/clock", ADMIN, {"days": 7})
    assert unit_id in [n["unit_id"] for n in week["notified"]]
    assert unit_id not in [d["unit_id"] for d in week["decided"]]
    assert db.get(Unit, unit_id).status == "on_shelf"  # уведомление остаток не меняет

    two = call(client, "/sim/clock", ADMIN, {"days": 7})
    [d] = [x for x in two["decided"] if x["unit_id"] == unit_id]
    assert d["action"] == "hub" and d["days"] == 14
    assert d["storage_kop"] == 15 * 2 * 14  # 4,20 ₽: 15 коп./л/сут × 2 л × 14 сут (docs/04)
    unit = db.get(Unit, unit_id)
    db.refresh(unit)
    assert unit.status == "to_hub"
    assert unit_id not in [n["unit_id"] for n in two["notified"]]  # второй раз не уведомляем

    again = call(client, "/sim/clock", ADMIN, {"days": 1})
    assert unit_id not in [x["unit_id"] for x in again["decided"]]  # вывоз один раз
    assert unbalanced_txn_ids(db) == []


def test_stale_refusal_moves_where_demand_is(client, db, city):
    # на B есть спрос на SKU-003: заказ забрал одну единицу из A, остальные залежались
    o = call(client, "/orders", ADMIN, {"sku_id": "SKU-003", "pvz_id": B})
    assert o["variant"] == "van"
    r = call(client, "/sim/clock", ADMIN, {"days": 14})
    moved = [d for d in r["decided"] if d["action"] == "move"]
    assert moved and all(d["to"] == B and d["from"] == A for d in moved)
    assert all(
        d["cost_kop"] == P.van_space_kop + P.receive_fee_kop < P.hub_return_kop for d in moved
    )
    unit = db.get(Unit, moved[0]["unit_id"])
    db.refresh(unit)
    assert (unit.node_id, unit.status) == (B, "on_shelf")
    assert unbalanced_txn_ids(db) == []


def test_notifications_endpoint(client, city):
    o = van_order(client)
    call(client, "/incidents", LOGIST, {"node_id": B, "buyer_accepts_alt": True})
    rows = client.get("/notifications?role=seller", headers=ADMIN).json()
    assert any(str(o["id"]) in n["text"] for n in rows)
