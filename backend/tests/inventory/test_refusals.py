"""Приём отказа в ПВЗ (N4): оценка A/B/C, лимит полки, идемпотентность, начисления."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.modules.economics.params import DEFAULTS as P
from app.modules.economics.params import kopecks
from app.modules.inventory.models import Product, ReductionDispatch, Return, Seller, Unit
from app.modules.ledger.models import LedgerEntry, LedgerTxn
from app.modules.ledger.service import unbalanced_txn_ids
from app.modules.network.models import Node
from app.modules.ops.models import SimClock

OP = {"X-Role": "pvz_operator"}
# числа из economics.params (N8): при 330 ₽/ч проверка 3 мин = 16,50 ₽, переупаковка 2 мин = 11 ₽
CHECK_LABOUR = kopecks(P.labour(P.inspection_min))
REPACK_LABOUR = kopecks(P.labour(P.repack_min))


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
    # МП платит ПВЗ тариф за проверку, ПВЗ платит за время сотрудника
    assert CHECK_LABOUR == 1650 and P.inspection_fee_kop == 1980
    assert legs(db, r["txn_ids"]) == sorted(
        [("mp", -1980), ("pvz:T-A", 1980), ("pvz:T-A", -1650), ("ext:labour", 1650)]
    )
    assert unbalanced_txn_ids(db) == []


def test_grade_b_is_repacked_and_pays_both(client, db, pvz):
    r = refuse(client, "B", "k-b").json()
    assert r["placement"] == "shelf" and r["decision"] == "repack"
    assert len(r["txn_ids"]) == 4  # проверка и переупаковка: тариф + труд каждая
    rows = legs(db, r["txn_ids"])
    assert ("ext:materials", P.pack_kop) in rows  # пакет 3,06 ₽ — реальная затрата
    pvz_net = sum(a for acc, a in rows if acc == "pvz:T-A")
    expected = (P.inspection_fee_kop - CHECK_LABOUR) + (
        P.repack_fee_kop - REPACK_LABOUR - P.pack_kop
    )
    assert pvz_net == expected == 550  # +3,30 ₽ на проверке, +2,20 ₽ на переупаковке


def test_grade_c_goes_to_hub_but_check_is_paid(client, db, pvz):
    r = refuse(client, "C", "k-c").json()
    assert r["placement"] == "hub" and "оценка C" in r["reason"]
    assert db.get(Unit, r["unit_id"]).status == "to_hub"
    assert len(r["txn_ids"]) == 2  # проверку оплачиваем при любой оценке
    assert r["used_l"] == 0.0


def shelf_tasks(client, pvz):
    return client.get(f"/pvz/{pvz}/tasks", headers=OP).json()["shelf"]


def writeoffs(client, pvz):
    return client.get(f"/pvz/{pvz}/stock", headers=OP).json()["writeoffs_today"]


def test_daily_writeoffs_are_scoped_to_day_point_and_reason(client, db, pvz):
    from app.seed import seed

    seed(db)
    db.get(Node, pvz).storage_total_l = 400  # полка на 100 л, хоть сама коробка и помещается
    db.flush()
    ten = refuse(client, "A", "exact", sku="SKU-BOX-10L").json()
    assert ten["placement"] == "shelf"
    large = refuse(client, "B", "large", sku="SKU-BOX-50L").json()
    assert (large["placement"], large["used_l"], len(large["txn_ids"])) == ("hub", 10, 2)
    assert refuse(client, "B", "large", sku="SKU-BOX-50L").json() == large
    broken = refuse(client, "C", "damaged").json()
    refuse(client, "A", "ineligible", sku="T-SOFA")
    rows = writeoffs(client, pvz)
    assert [(r["unit_id"], r["category"], r["barcode"], r["reason"]) for r in rows] == [
        (broken["unit_id"], "packaging", broken["barcode"], broken["reason"]),
        (large["unit_id"], "volume", large["barcode"], large["reason"]),
    ]
    assert (rows[1]["sku"], rows[1]["volume_l"]) == ("SKU-BOX-50L", 50)
    assert "50 л" in large["reason"] and "10 л" in large["reason"]
    assert not writeoffs(client, "T-Z") and not shelf_tasks(client, pvz)
    next_morning(client)
    assert not writeoffs(client, pvz)


def complete(client, pvz, task, key):
    return client.post(
        f"/pvz/{pvz}/units/{task['unit_id']}/{task['action']}",
        headers={**OP, "Idempotency-Key": key},
        json={"version": task["version"]},
    )


def test_full_shelf_queues_fifo_and_reuses_tasks(client, db, pvz):
    first = refuse(client, "A", "f1").json()
    second = refuse(client, "A", "f2").json()
    # У второй единицы более ранняя дата размещения, чем у первой.
    db.get(Unit, second["unit_id"]).shelved_at -= timedelta(days=2)
    db.flush()
    third = refuse(client, "B", "f3").json()
    assert refuse(client, "B", "f3").json() == third
    assert (third["placement"], third["used_l"], third["decision"]) == ("queue", 4, "repack")
    assert len(third["txn_ids"]) == 4
    ret = db.scalar(select(Return).where(Return.unit_id == third["unit_id"]))
    assert (ret.kind, ret.decision) == ("refusal", "repack")
    [remove] = shelf_tasks(client, pvz)
    assert (remove["unit_id"], remove["barcode"], remove["action"]) == (
        second["unit_id"],
        second["barcode"],
        "remove",
    )
    fourth = refuse(client, "A", "f4").json()
    assert fourth["placement"] == "queue"
    assert [t["unit_id"] for t in shelf_tasks(client, pvz)] == [second["unit_id"], first["unit_id"]]
    # Второй ожидающий не может занять место первого.
    assert (
        complete(
            client, pvz, dict(unit_id=fourth["unit_id"], action="shelve", version=1), "early"
        ).status_code
        == 409
    )
    assert complete(client, "T-Z", remove, "wrong-pvz").status_code == 404
    assert complete(client, pvz, {**remove, "version": 0}, "stale").status_code == 409
    answer = complete(client, pvz, remove, "done")
    assert answer.json()["status"] == "to_hub"
    assert complete(client, pvz, remove, "done").json() == answer.json()
    assert complete(client, pvz, remove, "repeat").status_code == 409
    remaining, place = shelf_tasks(client, pvz)
    assert remaining["unit_id"] == first["unit_id"]
    assert (place["action"], place["unit_id"]) == ("shelve", third["unit_id"])
    assert complete(client, pvz, place, "place").json()["status"] == "on_shelf"
    assert db.get(Unit, third["unit_id"]).shelved_at is not None
    assert not writeoffs(client, pvz)
    assert unbalanced_txn_ids(db) == []


def test_multiple_removals_release_actual_litres(client, db, pvz):
    a, b = [refuse(client, "A", key).json()["unit_id"] for key in ("a", "b")]
    db.add(Product(sku="T-BOX", seller_id="T-S1", volume_l=3.5, price_kop=100))
    db.flush()
    box = refuse(client, "A", "box", sku="T-BOX").json()["unit_id"]
    tasks = shelf_tasks(client, pvz)
    assert [(t["unit_id"], t["volume_l"]) for t in tasks] == [(a, 2), (b, 2)]
    stock = client.get(f"/pvz/{pvz}/stock", headers=OP).json()
    assert [u["status"] for u in stock["units"]] == [
        "remove_pending",
        "remove_pending",
        "awaiting_shelf",
    ]
    assert stock["used_l"] == 4
    network = client.get("/network", headers=OP).json()
    assert next(n for n in network["nodes"] if n["id"] == pvz)["shelf_used_l"] == 4
    assert complete(client, pvz, tasks[0], "r1").status_code == 200
    assert shelf_tasks(client, pvz) == tasks[1:]
    assert (
        complete(client, pvz, dict(unit_id=box, action="shelve", version=1), "no-room").status_code
        == 409
    )
    assert complete(client, pvz, tasks[1], "r2").status_code == 200
    [place] = shelf_tasks(client, pvz)
    assert (place["unit_id"], place["action"]) == (box, "shelve")
    assert complete(client, pvz, place, "p1").status_code == 200
    assert shelf_tasks(client, pvz) == []
    stock = client.get(f"/pvz/{pvz}/stock", headers=OP).json()
    assert stock["used_l"] == 3.5 and len(stock["units"]) == 1
    assert stock["units"][0]["status"] == "on_shelf"


def test_reserved_unit_is_protected_and_larger_limit_cancels_removal(client, db, pvz):
    reserved = refuse(client, "A", "reserved").json()["unit_id"]
    db.get(Unit, reserved).status = "reserved"
    db.flush()
    available = refuse(client, "A", "available").json()["unit_id"]
    incoming = refuse(client, "A", "incoming").json()["unit_id"]
    [task] = shelf_tasks(client, pvz)
    assert task["unit_id"] == available
    response = client.patch(
        f"/pvz/{pvz}/settings", headers={**OP, "Idempotency-Key": "up"}, json={"mesh_share_pct": 50}
    )
    assert response.json()["used_l"] == 4
    [place] = shelf_tasks(client, pvz)
    assert (place["action"], place["unit_id"]) == ("shelve", incoming)
    assert db.get(Unit, available).status == "on_shelf"
    assert db.get(Unit, reserved).status == "reserved"
    assert complete(client, pvz, task, "old-task").status_code == 409


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


def next_morning(client, key="morning", days=1):
    response = client.post(
        "/sim/clock", headers={"X-Role": "admin", "Idempotency-Key": key}, json={"days": days}
    )
    assert response.status_code == 200, response.text


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
    assert (down["mesh_share_pct"], down["pending_share_pct"], down["limit_l"]) == (50, 10, 8)
    assert down["estimated_days"] == 1 and not shelf_tasks(client, pvz)
    next_morning(client)
    stock = client.get(f"/pvz/{pvz}/stock", headers=OP).json()
    assert (stock["limit_l"], stock["mesh_share_pct"], stock["used_l"]) == (1.6, 10, 8)
    removals = shelf_tasks(client, pvz)
    assert len(removals) == 4 and all(t["action"] == "reduce" for t in removals)
    for task in removals:
        assert complete(client, pvz, task, f"done-{task['unit_id']}").status_code == 200
    settings = client.get("/pvz/T-A/stock", headers=OP).json()
    assert (settings["mesh_share_pct"], settings["pending_share_pct"], settings["limit_l"]) == (
        10,
        None,
        1.6,
    )
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


def test_empty_capacity_is_released_with_oversize_unit_waiting(client, db, pvz):
    old = refuse(client, "A", "old").json()["unit_id"]
    db.add(Product(sku="T-BOX", seller_id="T-S1", volume_l=3.5, price_kop=100))
    db.flush()
    box = refuse(client, "A", "box", sku="T-BOX").json()
    assert box["placement"] == "queue"

    # Новая полка — 2,4 л. На ней помещается старая чашка, но не ожидающая коробка.
    def change(share, key):
        return client.patch(
            f"/pvz/{pvz}/settings",
            headers={**OP, "Idempotency-Key": key},
            json={"mesh_share_pct": share},
        ).json()

    request = change(15, "down")
    assert request["mesh_share_pct"] == 15 and request["pending_share_pct"] is None
    assert request["estimated_days"] == 0
    [task] = shelf_tasks(client, pvz)
    assert (task["unit_id"], task["action"]) == (box["unit_id"], "return")
    assert db.get(Unit, old).status == "on_shelf"
    assert db.get(Unit, box["unit_id"]).status == "awaiting_return"
    stock = client.get(f"/pvz/{pvz}/stock", headers=OP).json()
    assert (stock["used_l"], stock["limit_l"], stock["pending_share_pct"]) == (2, 2.4, None)
    # Если оператор передумал, вернётся очередь размещения под прежний лимит.
    assert change(25, "keep")["pending_share_pct"] is None
    assert db.get(Unit, box["unit_id"]).status == "awaiting_shelf"
    assert complete(client, pvz, task, "cancelled-task").status_code == 409
    change(15, "again")
    [task] = shelf_tasks(client, pvz)
    assert not writeoffs(client, pvz)
    next_morning(client)
    assert complete(client, pvz, task, "confirm").json()["status"] == "to_hub"
    [row] = writeoffs(client, pvz)
    assert (row["unit_id"], row["category"]) == (box["unit_id"], "volume")
    assert client.get(f"/pvz/{pvz}/stock", headers=OP).json()["pending_share_reason"] is None
    applied = client.get(f"/pvz/{pvz}/stock", headers=OP).json()
    assert (applied["limit_l"], applied["used_l"], applied["pending_share_pct"]) == (2.4, 2, None)


def test_catalog_suggests_box_before_first_refusal(client, db, pvz):
    from app.seed import seed

    seed(db)
    node = db.get(Node, pvz)
    node.mesh_share_pct = 50  # лимит тестовой полки — 8 л
    db.flush()
    stock = client.get(f"/pvz/{pvz}/stock", headers=OP).json()
    assert stock["units"] == []
    boxes = [product for product in stock["products"] if product["sku"].startswith("SKU-BOX-")]
    assert boxes == [
        {"sku": f"SKU-BOX-{litres}L", "name": "Коробка для хранения", "volume_l": litres}
        for litres in (50, 10, 8, 5, 3)
    ]
    first = refuse(client, "A", "box-on-shelf", sku="SKU-BOX-5L", pvz=pvz).json()
    second = refuse(client, "A", "second-box", sku="SKU-BOX-5L", pvz=pvz).json()
    assert (first["placement"], first["used_l"]) == ("shelf", 5)
    assert (second["placement"], second["used_l"]) == ("queue", 5)


def test_operator_advances_one_shared_day_once(client, pvz):
    from datetime import date

    def day(point):
        stock = client.get(f"/pvz/{point}/stock", headers=OP)
        return date.fromisoformat(stock.json()["simulation_day"])

    today = day(pvz)
    path = "/sim/clock/next"
    body = {"expected_day": today.isoformat()}
    headers = {**OP, "Idempotency-Key": "next-day"}
    assert client.post(path, headers=OP, json=body).status_code == 422
    assert client.post(path, headers={**headers, "X-Role": "seller"}, json=body).status_code == 403
    assert client.post("/sim/clock", headers=headers, json={"days": 7}).status_code == 403
    first = client.post(path, headers=headers, json=body)
    assert first.status_code == 200
    assert client.post(path, headers=headers, json=body).json() == first.json()
    stale = client.post(path, headers={**OP, "Idempotency-Key": "other-tab"}, json=body)
    assert stale.status_code == 409
    assert day(pvz) == day("T-Z") == today + timedelta(days=1)


def test_daily_reduction_fifo_sales_and_midnight(client, db, pvz):
    from datetime import date

    node = db.get(Node, pvz)
    node.storage_total_l = 100
    node.mesh_share_pct = 50
    db.flush()
    ids = []
    # Полка на 50 л, фактически 31 л. Цель 10 л — освобождаем старейший префикс.
    for i, litres in enumerate([8, 5, 3, 5, 10]):
        sku = f"T-RED-{i}"
        db.add(Product(sku=sku, seller_id="T-S1", volume_l=litres, price_kop=150000))
        db.flush()
        ids.append(refuse(client, "A", sku, sku=sku).json()["unit_id"])

    def change(share, key):
        return client.patch(
            f"/pvz/{pvz}/settings",
            headers={**OP, "Idempotency-Key": key},
            json={"mesh_share_pct": share},
        ).json()

    def tasks():
        return client.get(f"/pvz/{pvz}/tasks", headers=OP).json()

    result = change(10, "shrink")
    assert (result["limit_l"], result["mesh_share_pct"], result["estimated_days"]) == (31, 31, 3)
    assert not tasks()["shelf"]
    shown = client.get(f"/pvz/{pvz}/stock", headers=OP).json()["simulation_day"]
    step = client.post(
        "/sim/clock/next",
        headers={**OP, "Idempotency-Key": "morning"},
        json={"expected_day": shown},
    )
    assert step.status_code == 200
    plan = tasks()
    today = date.fromisoformat(plan["reduction"]["day"])
    [first] = plan["shelf"]
    assert (first["unit_id"], first["scheduled_for"]) == (ids[0], today.isoformat())
    live = client.get(f"/pvz/{pvz}/stock", headers=OP).json()
    assert (live["mesh_share_pct"], live["limit_l"], live["used_l"]) == (23, 23, 31)
    assert live["estimated_days"] == 2
    assert complete(client, pvz, {**first, "action": "remove"}, "bypass").status_code == 409
    # Оператор не трогал первую коробку. Второе утро всё равно уменьшает долю на 5 + 3 л.
    next_morning(client, "second-morning")
    first, second, third = tasks()["shelf"]
    assert [t["unit_id"] for t in (first, second, third)] == ids[:3]
    tomorrow = (today + timedelta(days=1)).isoformat()
    assert second["scheduled_for"] == third["scheduled_for"] == tomorrow
    assert client.get(f"/pvz/{pvz}/stock", headers=OP).json()["mesh_share_pct"] == 15
    assert complete(client, pvz, third, "out-of-order").status_code == 409
    done = complete(client, pvz, first, "send-first")
    assert done.json()["status"] == "to_hub"
    assert complete(client, pvz, first, "send-first").json() == done.json()
    assert complete(client, pvz, second, "daily-cap").status_code == 409
    r = client.post(
        "/orders",
        headers={"X-Role": "admin", "Idempotency-Key": "buy-planned"},
        json={"sku_id": "T-RED-1", "pvz_id": pvz},
    )
    assert r.status_code == 200, r.text
    order = r.json()
    assert order["variant"] == "own"
    assert [t["unit_id"] for t in tasks()["shelf"]] == [ids[2]]
    assert complete(client, pvz, second, "sold").status_code == 409
    picked = client.post(
        f"/orders/{order['id']}/pick",
        headers={**OP, "Idempotency-Key": "pick"},
        json={"version": order["version"]},
    ).json()
    assert picked["status"] == "ready"
    assert tasks()["reduction"]["sent_l"] == 8
    next_morning(client, "third-morning")
    assert client.get(f"/pvz/{pvz}/stock", headers=OP).json()["mesh_share_pct"] == 10
    third, fourth = tasks()["shelf"]
    assert [third["unit_id"], fourth["unit_id"]] == ids[2:4]
    assert complete(client, pvz, third, "third").status_code == 200
    assert complete(client, pvz, fourth, "fourth").status_code == 200
    assert tasks()["reduction"] is None
    stock = client.get(f"/pvz/{pvz}/stock", headers=OP).json()
    assert (stock["used_l"], stock["limit_l"], stock["pending_share_pct"]) == (10, 10, None)


def test_sale_releases_capacity_on_next_day_without_spending_daily_limit(client, db, pvz):
    db.get(Node, pvz).storage_total_l = 40
    db.flush()
    ids = [refuse(client, "A", f"cup-{i}").json()["unit_id"] for i in range(3)]
    client.patch(
        f"/pvz/{pvz}/settings",
        headers={**OP, "Idempotency-Key": "target-four"},
        json={"mesh_share_pct": 10},
    )
    assert not shelf_tasks(client, pvz)
    scheduled = {"unit_id": ids[0], "version": 1, "action": "reduce"}
    order = client.post(
        "/orders",
        headers={"X-Role": "admin", "Idempotency-Key": "buy-oldest"},
        json={"pvz_id": pvz, "sku_id": "T-CUP"},
    ).json()
    assert order["variant"] == "own"
    assert shelf_tasks(client, pvz) == []
    assert complete(client, pvz, scheduled, "stale").status_code == 409
    assert db.get(Node, pvz).pending_share_pct == 10  # резерв пока физически на полке
    assert (
        client.post(
            f"/orders/{order['id']}/pick",
            headers={**OP, "Idempotency-Key": "pick"},
            json={"version": order["version"]},
        ).status_code
        == 200
    )
    stock = client.get(f"/pvz/{pvz}/stock", headers=OP).json()
    assert (stock["used_l"], stock["limit_l"], stock["pending_share_pct"]) == (4, 6, 10)
    assert stock["estimated_days"] == 1
    next_morning(client)
    assert client.get(f"/pvz/{pvz}/stock", headers=OP).json()["mesh_share_pct"] == 10
    assert db.get(Node, pvz).pending_share_pct is None
    assert (
        db.scalar(
            select(func.count())
            .select_from(ReductionDispatch)
            .where(ReductionDispatch.pvz_id == pvz)
        )
        == 0
    )


def test_queue_returns_keep_morning_capacity_and_do_not_spend_daily_budget(client, db, pvz):
    from app.modules.inventory.shelf_queue import PVZ_TIMEZONE

    clock = db.get(SimClock, 1)
    clock.base_at -= timedelta(days=3)  # полночь реального календаря уже давно прошла
    shown = clock.base_at.astimezone(PVZ_TIMEZONE).date() + timedelta(days=clock.offset_days)
    node = db.get(Node, pvz)
    node.storage_total_l = 100
    node.mesh_share_pct = 50
    db.add(Product(sku="T-BOX", seller_id="T-S1", volume_l=10, price_kop=100))
    db.flush()
    for i in range(4):
        refuse(client, "A", f"old-{i}", sku="T-BOX")
    client.patch(
        f"/pvz/{pvz}/settings", headers={**OP, "Idempotency-Key": "shrink"},
        json={"mesh_share_pct": 10},
    )

    def stock():
        return client.get(f"/pvz/{pvz}/stock", headers=OP).json()

    def tasks():
        return client.get(f"/pvz/{pvz}/tasks", headers=OP).json()

    def advance(key):
        res = client.post(
            "/sim/clock/next", headers={**OP, "Idempotency-Key": key},
            json={"expected_day": stock()["simulation_day"]},
        )
        assert res.status_code == 200

    assert stock()["simulation_day"] == shown.isoformat()
    assert stock()["limit_l"] == 40 and not tasks()["shelf"]
    advance("first-day")
    [scheduled] = tasks()["shelf"]
    assert scheduled["action"] == "reduce"
    assert complete(client, pvz, scheduled, "planned").status_code == 200
    for i in range(2):
        incoming = refuse(client, "A", f"in-{i}", sku="T-BOX").json()
        assert incoming["placement"] == "queue"
        [removal] = tasks()["shelf"]
        assert removal["action"] == "remove"
        assert complete(client, pvz, removal, f"normal-{i}").status_code == 200
        assert stock()["used_l"] == 20 and stock()["limit_l"] == 30
        [placement] = tasks()["shelf"]
        assert (placement["action"], placement["unit_id"]) == ("shelve", incoming["unit_id"])
        assert complete(client, pvz, placement, f"place-{i}").status_code == 200
        assert stock()["limit_l"] == stock()["used_l"] == 30
        assert stock()["simulation_day"] == (shown + timedelta(days=1)).isoformat()
        assert not tasks()["shelf"] and tasks()["reduction"]["sent_l"] == 10
    advance("second-day")
    [scheduled] = tasks()["shelf"]
    assert scheduled["action"] == "reduce" and scheduled["volume_l"] == 10
    assert stock()["limit_l"] == 20 and tasks()["reduction"]["sent_l"] == 0


def test_missed_mornings_and_current_capacity(client, db, pvz):
    from app.modules.inventory.service import sim_now
    from app.modules.inventory.shelf_queue import PVZ_TIMEZONE, advance_mornings

    node = db.get(Node, pvz)
    node.storage_total_l = 200
    node.mesh_share_pct = 25
    db.flush()
    db.add(Product(sku="T-TEN", seller_id="T-S1", volume_l=10, price_kop=150000))
    db.flush()
    for i in range(5):
        refuse(client, "A", f"ten-{i}", sku="T-TEN")
    result = client.patch(
        f"/pvz/{pvz}/settings",
        headers={**OP, "Idempotency-Key": "reduce-30"},
        json={"mesh_share_pct": 10},
    ).json()
    assert result["estimated_days"] == 3
    today = sim_now(db).astimezone(PVZ_TIMEZONE).date()
    advance_mornings(db, today + timedelta(days=2))
    advance_mornings(db, today + timedelta(days=2))  # повтор запуска в тот же день
    tasks = shelf_tasks(client, pvz)
    assert [row["scheduled_for"] for row in tasks] == [
        (today + timedelta(days=1)).isoformat(),
        (today + timedelta(days=2)).isoformat(),
    ]
    assert node.active_share_pct == 15 and node.pending_share_pct == 10
    assert node.mesh_limit_l == 30
    # Обычный товар принимается в очередь на действующей полке, крупногабаритный — в хаб.
    standard = refuse(client, "A", "ordinary", sku="T-TEN").json()
    assert standard["placement"] == "queue" and standard["limit_l"] == 30
    db.add(Product(sku="T-25L", seller_id="T-S1", volume_l=25, price_kop=150000))
    db.flush()
    incoming = refuse(client, "A", "current-capacity", sku="T-25L").json()
    assert incoming["placement"] == "hub" and incoming["limit_l"] == 30
