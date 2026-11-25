"""Проверки алгоритма логистики на маленьких городах, где ответ известен заранее."""

from fastapi.testclient import TestClient

from app.main import app
from app.modules.routing.compare import compare
from app.modules.routing.daily import choose, close_pvz, day_state
from app.modules.routing.demo import demo_city
from app.modules.routing.models import (
    City,
    Forecast,
    Node,
    NodeKind,
    Order,
    Sku,
    StockItem,
    Stop,
    VanRoute,
    Variant,
)
from app.modules.routing.network import Capacity
from app.modules.routing.nightly import nightly_plan

NODES = [
    Node(id="HUB", kind=NodeKind.HUB, x_km=-15, y_km=0),
    Node(id="A", x_km=0, y_km=0),
    Node(id="C", x_km=0.5, y_km=0),
    Node(id="B", x_km=1, y_km=0),
    Node(id="T", x_km=3, y_km=3),
    Node(id="X", x_km=1.5, y_km=1),
    Node(id="Z", x_km=5, y_km=5),
]


def route(rid, stops, capacity=300):
    return VanRoute(
        id=rid,
        depot_id="HUB",
        capacity=capacity,
        stops=[Stop(node_id=n, eta_min=eta) for n, eta in stops],
    )


def city(routes, stock, orders, forecast=(), nodes=NODES):
    return City(
        nodes=list(nodes),
        routes=routes,
        skus=[Sku(id="S")],
        stock=[StockItem(node_id=n, sku_id="S", qty=q) for n, q in stock],
        orders=orders,
        forecast=list(forecast),
    )


def by_order(plan):
    return {a.order_id: a for a in plan.assignments}


def test_own_pvz_first():
    plan, _ = nightly_plan(
        city(
            [route("R", [("A", 60), ("B", 120)])],
            [("A", 5)],
            [Order(id="o", sku_id="S", pvz_id="A")],
        )
    )
    assert by_order(plan)["o"].variant == Variant.OWN


def test_van_only_in_route_direction():
    r = [route("R", [("A", 60), ("B", 120)])]
    forward, _ = nightly_plan(city(r, [("A", 5)], [Order(id="o", sku_id="S", pvz_id="B")]))
    a = by_order(forward)["o"]
    assert a.variant == Variant.VAN
    assert [(leg.from_node, leg.to_node) for leg in a.legs] == [("A", "B")]
    assert a.money_kop == 2490 + 710

    backward, _ = nightly_plan(city(r, [("B", 5)], [Order(id="o", sku_id="S", pvz_id="A")]))
    assert by_order(backward)["o"].variant == Variant.HUB  # газель уже проехала A


def test_van_capacity_respected():
    r = [route("R", [("A", 60), ("B", 120)], capacity=2)]
    orders = [Order(id=f"o{i}", sku_id="S", pvz_id="B") for i in range(3)]
    plan, _ = nightly_plan(city(r, [("A", 10)], orders))
    variants = sorted(a.variant for a in plan.assignments for _ in range(a.qty))
    assert variants.count(Variant.VAN) == 2
    assert variants.count(Variant.HUB) == 1


def test_transfer_between_vans():
    r = [route("R1", [("A", 60), ("T", 80)]), route("R2", [("T", 110), ("B", 130)])]
    plan, _ = nightly_plan(city(r, [("A", 5)], [Order(id="o", sku_id="S", pvz_id="B")]))
    a = by_order(plan)["o"]
    assert a.variant == Variant.VAN
    assert [leg.route_id for leg in a.legs] == ["R1", "R2"]
    assert a.money_kop == 2490 + 710 + 1300


def test_detour_only_when_enough_units():
    r = [route("R", [("A", 60), ("B", 120)])]
    one, _ = nightly_plan(city(r, [("A", 20)], [Order(id="o", sku_id="S", pvz_id="X")]))
    assert one.inserted_stops == []
    assert by_order(one)["o"].variant == Variant.HUB

    many, _ = nightly_plan(city(r, [("A", 20)], [Order(id="o", sku_id="S", qty=8, pvz_id="X")]))
    assert [s.node_id for s in many.inserted_stops] == ["X"]
    ins = many.inserted_stops[0]
    assert ins.added_min <= 20 and ins.gain_kop > 0
    assert by_order(many)["o"].variant == Variant.VAN


def test_detour_respects_time_limit():
    r = [route("R", [("A", 60), ("B", 120)])]
    plan, _ = nightly_plan(city(r, [("A", 50)], [Order(id="o", sku_id="S", qty=40, pvz_id="Z")]))
    assert plan.inserted_stops == []  # Z слишком далеко: крюк больше лимита
    assert by_order(plan)["o"].variant == Variant.HUB


def test_rebalance_to_forecast_deficit():
    r = [route("R", [("A", 60), ("B", 120)])]
    plan, _ = nightly_plan(
        city(r, [("A", 10)], [], forecast=[Forecast(node_id="B", sku_id="S", daily_mean=2)])
    )
    moved = sum(m.qty for m in plan.movements if m.from_node == "A" and m.to_node == "B")
    assert moved == 4  # цель B = 2 шт./день × 2 дня


def test_van_passes_closed_pvz():
    nodes = [n if n.id != "C" else n.model_copy(update={"is_open": False}) for n in NODES]
    r = [route("R", [("A", 60), ("C", 90), ("B", 120)])]
    plan, _ = nightly_plan(
        city(r, [("A", 5)], [Order(id="o", sku_id="S", pvz_id="B")], nodes=nodes)
    )
    assert by_order(plan)["o"].variant == Variant.VAN


def _day(c):
    nodes = {n.id: n for n in c.nodes}
    stock = {(s.node_id, s.sku_id): s.qty for s in c.stock}
    return day_state(c, c.routes, Capacity.from_routes(c.routes, nodes, c.params), stock, [])


def test_daily_choice_explains_rejections():
    c = city([route("R", [("A", 60), ("B", 120)])], [("B", 5)], [])
    d = choose(_day(c), Order(id="o", sku_id="S", pvz_id="A"))
    assert d.assignment.variant == Variant.HUB
    van = next(x for x in d.candidates if x.variant == Variant.VAN)
    assert "нет попутной газели" in van.reason
    own = next(x for x in d.candidates if x.variant == Variant.OWN)
    assert "нет свободного остатка" in own.reason


def test_daily_choice_respects_deadline():
    c = city([route("R", [("A", 60), ("B", 120)])], [("A", 5)], [])
    tight = choose(_day(c), Order(id="o", sku_id="S", pvz_id="B", max_hours=6))
    assert tight.assignment is None  # ни газель (~12 ч), ни хаб (36 ч) не успевают
    van = next(x for x in tight.candidates if x.variant == Variant.VAN)
    assert "хуже обещанного" in van.reason

    ok = choose(_day(c), Order(id="o", sku_id="S", pvz_id="B", max_hours=24))
    assert ok.assignment.variant == Variant.VAN


def test_neighbor_needs_consent():
    c = city([route("R", [("A", 60), ("B", 120)])], [("C", 5)], [])
    st = _day(c)
    no = choose(st, Order(id="o1", sku_id="S", pvz_id="A"))
    assert no.assignment.variant == Variant.HUB
    yes = choose(st, Order(id="o2", sku_id="S", pvz_id="A", accepts_neighbor=True))
    assert yes.assignment.variant == Variant.NEIGHBOR


def test_closure_reroutes_van_orders():
    c = city([route("R", [("A", 60), ("B", 120), ("X", 140)])], [("A", 5)], [])
    st = _day(c)
    choose(st, Order(id="o", sku_id="S", pvz_id="B"))
    tomorrow = close_pvz(st, "B", buyer_accepts_alt=False)[0]
    assert tomorrow.extra_hours == 24 and tomorrow.penalty_kop == 2000
    assert len(tomorrow.notifications) == 2

    st = _day(c)
    choose(st, Order(id="o", sku_id="S", pvz_id="B"))
    alt = close_pvz(st, "B", buyer_accepts_alt=True)[0]
    chosen = next(x for x in alt.candidates if x.chosen)
    assert chosen.source_node == "X" and alt.extra_hours < 1


def test_nightly_beats_greedy_beats_hub_on_demo():
    hub, greedy, nightly = compare(demo_city())
    assert nightly.money_kop <= greedy.money_kop < hub.money_kop
    assert nightly.avg_hours <= greedy.avg_hours < hub.avg_hours
    assert nightly.local_share >= greedy.local_share


def test_api_demo():
    client = TestClient(app)
    h = {"X-Role": "logist"}
    assert client.post("/routing/demo/reset", headers=h).status_code == 200
    schemes = client.get("/routing/demo/compare", headers=h).json()
    assert [s["scheme"] for s in schemes] == [
        "Всё через хаб",
        "По одному заказу (жадно)",
        "Ночной план",
    ]
    d = client.post(
        "/routing/demo/orders",
        headers=h,
        json={"id": "new", "sku_id": "SKU-001", "pvz_id": "ПВЗ-C"},
    ).json()
    assert any(c["chosen"] for c in d["candidates"])
    assert client.get("/routing/demo").status_code == 401


# ---------------------------------------------------------------- исправленные ошибки


def test_inserted_stop_waits_for_service_at_previous_stop():
    r = VanRoute(
        id="R",
        depot_id="HUB",
        stops=[
            Stop(node_id="A", eta_min=60, service_min=12),
            Stop(node_id="B", eta_min=80, service_min=12),
        ],
    )
    plan, _ = nightly_plan(city([r], [("A", 20)], [Order(id="o", sku_id="S", qty=8, pvz_id="X")]))
    route = plan.routes[0]
    k = next(k for k, s in enumerate(route.stops) if s.node_id == "X")
    prev = route.stops[k - 1]
    assert route.stops[k].eta_min > prev.eta_min + prev.service_min  # уехали после выгрузки


def test_shelf_limit_is_shared_between_skus():
    nodes = [*NODES[:-1], Node(id="Z", x_km=1.2, y_km=0, shelf_free_l=10)]
    c = City(
        nodes=nodes,
        routes=[route("R", [("A", 60), ("Z", 120)])],
        skus=[Sku(id="S1"), Sku(id="S2")],
        stock=[StockItem(node_id="A", sku_id=s, qty=10) for s in ("S1", "S2")],
        forecast=[Forecast(node_id="Z", sku_id=s, daily_mean=3) for s in ("S1", "S2")],
    )
    plan, _ = nightly_plan(c)
    assert sum(m.qty for m in plan.movements) * 2.0 <= 10  # по 2 л, полка 10 л


def test_stock_is_conserved():
    c = demo_city()
    plan, ev = nightly_plan(c)
    before: dict = {}
    for s in c.stock:
        before[(s.node_id, s.sku_id)] = before.get((s.node_id, s.sku_id), 0) + s.qty
    used = sum(a.qty for a in plan.assignments if a.variant != Variant.HUB)
    moved = sum(m.qty for m in plan.movements)
    assert sum(ev.stock_after.values()) == sum(before.values()) - used - moved
    assert sum(ev.incoming.values()) == moved
    assert min(ev.stock_after.values()) >= 0


def test_unknown_references_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="нет артикула"):
        city([], [], [Order(id="o", sku_id="NOPE", pvz_id="A")])
    client = TestClient(app)
    bad = {
        "nodes": [],
        "routes": [],
        "skus": [],
        "stock": [],
        "orders": [{"id": "o", "sku_id": "S", "pvz_id": "A"}],
    }
    assert client.post("/routing/plan", headers={"X-Role": "logist"}, json=bad).status_code == 422


def test_closure_offers_stop_not_yet_passed_on_demo():
    """Сценарий 4: G1 едет A → C → B; B закрылся сразу после посадки в A — C ещё впереди."""
    c = demo_city()
    plan, ev = nightly_plan(c)
    st = day_state(c, plan.routes, ev.cap, ev.stock_after, plan.assignments)
    d = next(x for x in close_pvz(st, "ПВЗ-B", buyer_accepts_alt=True) if x.order_id == "O-1")
    chosen = next(x for x in d.candidates if x.chosen)
    assert chosen.source_node == "ПВЗ-C"

    st = day_state(c, plan.routes, ev.cap, ev.stock_after, plan.assignments)
    late = close_pvz(st, "ПВЗ-B", buyer_accepts_alt=True, now_min=70)  # C уже проехали
    chosen = next(x for x in late[0].candidates if x.chosen)
    assert chosen.source_node != "ПВЗ-C"

    st = day_state(c, plan.routes, ev.cap, ev.stock_after, plan.assignments)
    assert close_pvz(st, "ПВЗ-B", buyer_accepts_alt=True, now_min=200) == []  # уже выдали


def test_closure_of_source_replans_order():
    c = city([route("R", [("A", 60), ("C", 90), ("B", 120)])], [("A", 1), ("C", 1)], [])
    st = _day(c)
    choose(st, Order(id="o", sku_id="S", pvz_id="B"))
    d = close_pvz(st, "A", buyer_accepts_alt=False)[0]
    new = next(a for a in st.assignments if a.order_id == "o")
    assert new.source_node == "C" and new.variant == Variant.VAN
    assert d.penalty_kop == 2000
    assert st.stock[("A", "S")] == 1  # товар остался в закрытом ПВЗ


def test_greedy_keeps_orders_without_feasible_variant():
    c = city(
        [route("R", [("A", 60), ("B", 120)])],
        [("A", 5)],
        [Order(id="o", sku_id="S", pvz_id="B", max_hours=6)],
    )
    hub, greedy, _ = compare(c)
    assert greedy.hub_units == 1 and greedy.money_kop == hub.money_kop


def test_demo_order_is_idempotent():
    client = TestClient(app)
    h = {"X-Role": "logist"}
    client.post("/routing/demo/reset", headers=h)
    order = {"id": "idem", "sku_id": "SKU-005", "pvz_id": "ПВЗ-H"}
    first = client.post("/routing/demo/orders", headers={**h, "Idempotency-Key": "k1"}, json=order)
    again = client.post("/routing/demo/orders", headers={**h, "Idempotency-Key": "k1"}, json=order)
    assert first.json() == again.json()
    dup = client.post("/routing/demo/orders", headers=h, json=order)
    assert dup.status_code == 409
