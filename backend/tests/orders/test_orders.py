"""Сквозной заказ (M1): выбор варианта, резерв FIFO, статусы с версией, проводки."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.modules.economics.params import routing_params
from app.modules.inventory.models import Unit
from app.modules.ledger.models import LedgerEntry, LedgerTxn
from app.modules.ledger.service import unbalanced_txn_ids
from app.modules.orders import pricing
from app.modules.orders.models import Reservation
from app.seed import seed

ADMIN = {"X-Role": "admin"}
OP = {"X-Role": "pvz_operator"}
A, B = "ПВЗ-A", "ПВЗ-B"


@pytest.fixture
def city(db):
    seed(db)  # демо-город N2: газель G1 едет A → C → B → E, на полке A — 20 отказов SKU-001
    db.flush()


def on_shelf(db, node, sku="SKU-001"):
    q = select(func.count()).where(Unit.node_id == node, Unit.sku == sku, Unit.status == "on_shelf")
    return db.scalar(q)


def order(client, pvz, sku="SKU-001", key=None, **extra):
    r = client.post(
        "/orders",
        headers={**ADMIN, "Idempotency-Key": key or uuid4().hex},  # как фронт: UUID
        json={"sku_id": sku, "pvz_id": pvz, **extra},
    )
    assert r.status_code == 200, r.text
    return r.json()


def step(client, o, action, expect=200):
    r = client.post(
        f"/orders/{o['id']}/{action}",
        headers={**OP, "Idempotency-Key": f"{o['id']}-{action}-{o['version']}"},
        json={"version": o["version"]},
    )
    assert r.status_code == expect, r.text
    return r.json()


def sums(db, order_id, scheme):
    rows = db.execute(
        select(LedgerEntry.account, func.sum(LedgerEntry.amount_kop))
        .join(LedgerTxn, LedgerTxn.id == LedgerEntry.txn_id)
        .where(LedgerTxn.order_id == order_id, LedgerTxn.scheme == scheme)
        .group_by(LedgerEntry.account)
    ).all()
    return dict(rows)


def test_scenario_1_refusal_in_a_sold_in_b_by_van(client, db, city):
    # отказ при получении в ПВЗ-A: товар не вскрыт, остаётся на полке
    r = client.post(
        f"/pvz/{A}/refusals",
        headers={**OP, "Idempotency-Key": "s1-refusal"},
        json={"sku_id": "SKU-001", "grade": "A"},
    )
    assert r.json()["placement"] == "shelf"
    before = on_shelf(db, A)

    o = order(client, B, key="s1-order")
    assert (o["variant"], o["source_node"], o["status"]) == ("van", A, "reserved")
    p = routing_params()
    assert o["cost_kop"] == p.own_kop + p.move_kop  # цена исполнения попутной газелью
    by = {c["variant"]: c for c in o["candidates"]}
    assert "нет свободного остатка" in by["own"]["reason"]
    assert by["van"]["chosen"] and by["van"]["legs"][0]["route_id"] == "G1"
    assert by["hub"]["money_kop"] == p.hub_kop
    assert on_shelf(db, A) == before - 1  # остаток A −1

    # FIFO: зарезервирована самая старая единица, а не только что принятый отказ
    unit_id = db.scalar(select(Reservation.unit_id).where(Reservation.order_id == o["id"]))
    oldest = db.scalar(select(func.min(Unit.shelved_at)).where(Unit.node_id == A))
    assert db.get(Unit, unit_id).shelved_at == oldest

    tasks = client.get(f"/pvz/{A}/tasks", headers=OP).json()
    assert [t["order_id"] for t in tasks["to_pick"]] == [o["id"]]
    o = step(client, o, "pick")
    assert o["status"] == "picked"
    assert client.get(f"/pvz/{A}/tasks", headers=OP).json()["to_load"][0]["van"] == "G1"
    o = step(client, o, "load")
    assert o["status"] == "in_transit"
    assert [
        t["order_id"] for t in client.get(f"/pvz/{B}/tasks", headers=OP).json()["to_receive"]
    ] == [o["id"]]
    o = step(client, o, "receive")
    o = step(client, o, "issue")
    assert o["status"] == "issued"

    unit = db.get(Unit, unit_id)
    db.refresh(unit)
    assert (unit.status, unit.node_id) == ("issued", B)
    assert not db.scalar(select(Reservation.active).where(Reservation.unit_id == unit_id))

    # деньги: всё сходится, продавец в mesh получает больше на скидку за местное исполнение
    assert unbalanced_txn_ids(db) == []
    mesh, base = sums(db, o["id"], "mesh"), sums(db, o["id"], "base")
    price = 150000
    commission = pricing.pct(price, pricing.COMMISSION_PCT)
    assert mesh["seller:S-01"] == price - commission - pricing.SELLER_LOGISTICS_MESH_KOP
    assert base["seller:S-01"] == price - commission - pricing.SELLER_LOGISTICS_BASE_KOP
    assert mesh["ext:buyer"] == base["ext:buyer"] == -price  # покупатель платит одинаково
    mesh_costs = sum(v for a, v in mesh.items() if a.startswith("ext:") and a != "ext:buyer")
    base_costs = sum(v for a, v in base.items() if a.startswith("ext:") and a != "ext:buyer")
    assert mesh_costs < base_costs  # реальных затрат меньше: нет обратной дороги и хаба
    assert mesh[f"pvz:{B}"] > 0 and mesh[f"pvz:{A}"] != 0


def test_own_shelf_goes_straight_to_issue(client, db, city):
    o = order(client, A)
    assert o["variant"] == "own"
    o = step(client, o, "pick")
    assert o["status"] == "ready"  # со своей полки — сразу в ячейку выдачи
    assert step(client, o, "issue")["status"] == "issued"


def test_hub_order_when_no_city_stock(client, db, city):
    o = order(client, B, sku="SKU-002")  # SKU-002 есть только в хабе
    assert (o["variant"], o["status"]) == ("hub", "in_transit")
    o = step(client, o, "receive")
    step(client, o, "issue")
    mesh, base = sums(db, o["id"], "mesh"), sums(db, o["id"], "base")
    assert mesh["ext:hub_ops"] == base["ext:hub_ops"]  # без отказа в городе схемы совпадают
    assert unbalanced_txn_ids(db) == []


def test_wrong_transition_is_409(client, city):
    o = order(client, B)
    r = client.post(
        f"/orders/{o['id']}/issue",
        headers={**OP, "Idempotency-Key": "x"},
        json={"version": o["version"]},
    )
    assert r.status_code == 409 and "Нельзя перевести" in r.json()["detail"]
    step(client, o, "load", expect=409)  # грузить можно только после отбора


def test_stale_version_is_409(client, city):
    o = order(client, B)
    r = client.post(
        f"/orders/{o['id']}/pick",
        headers={**OP, "Idempotency-Key": "y"},
        json={"version": o["version"] + 1},
    )
    assert r.status_code == 409 and "версия" in r.json()["detail"]


def test_same_key_same_order(client, db, city):
    first = order(client, B, key="same")
    again = order(client, B, key="same")
    assert again == first
    assert db.scalar(select(func.count()).where(Reservation.order_id == first["id"])) == 1


def test_orders_never_share_a_unit(client, db, city):
    ids = [order(client, B, key=f"many-{i}")["id"] for i in range(3)]
    units = db.scalars(select(Reservation.unit_id).where(Reservation.order_id.in_(ids))).all()
    assert len(units) == len(set(units)) == 3


def test_cancel_returns_unit_and_reverses_shadow(client, db, city):
    before = on_shelf(db, A)
    o = order(client, B, key="to-cancel")
    assert step(client, o, "cancel")["status"] == "cancelled"
    assert on_shelf(db, A) == before
    assert all(v == 0 for v in sums(db, o["id"], "base").values())  # тень снята сторно


def test_only_buyer_stub_roles_create_orders(client, city):
    r = client.post(
        "/orders",
        headers={"X-Role": "seller", "Idempotency-Key": "z"},
        json={"sku_id": "SKU-001", "pvz_id": B},
    )
    assert r.status_code == 403
