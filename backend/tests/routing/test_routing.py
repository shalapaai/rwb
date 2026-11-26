"""Проверки алгоритма логистики на маленьких городах, где ответ известен заранее."""

import pytest
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


def test_van_only_in_route_direction_without_detours():
    r = [route("R", [("A", 60), ("B", 120)])]
    forward, _ = nightly_plan(city(r, [("A", 5)], [Order(id="o", sku_id="S", pvz_id="B")]))
    a = by_order(forward)["o"]
    assert a.variant == Variant.VAN
    assert [(leg.from_node, leg.to_node) for leg in a.legs] == [("A", "B")]
    assert a.money_kop == 2490 + 710

    c = city(r, [("B", 5)], [Order(id="o", sku_id="S", pvz_id="A")])
    c.params.max_new_stops = 0
    backward, _ = nightly_plan(c)
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


def return_city(source: str, qty: int, available: int) -> City:
    return City(
        nodes=[
            Node(id="HUB", kind=NodeKind.HUB, x_km=-15, y_km=0),
            Node(id="ПВЗ-D", x_km=1.2, y_km=9.5),
            Node(id="ПВЗ-F", x_km=3, y_km=5.6),
            Node(id="ПВЗ-G", x_km=5.8, y_km=8.7),
            Node(id="ПВЗ-X", x_km=10.25, y_km=8.4),
        ],
        routes=[VanRoute(id="G2", depot_id="HUB", stops=[
            Stop(node_id="ПВЗ-D", eta_min=41, service_min=12),
            Stop(node_id="ПВЗ-F", eta_min=62, service_min=12),
            Stop(node_id="ПВЗ-G", eta_min=83, service_min=12),
        ])],
        skus=[Sku(id="SKU-002"), Sku(id="SKU-003")],
        stock=[StockItem(node_id=source, sku_id="SKU-003", qty=available)],
        orders=[Order(id="return", sku_id="SKU-003", qty=qty, pvz_id="ПВЗ-D")],
    )


@pytest.mark.parametrize(("source", "depart"), [("ПВЗ-F", 62), ("ПВЗ-G", 83)])
def test_profitable_return_visit_uses_direct_leg(source, depart):
    c = return_city(source, qty=3, available=3)
    original = c.model_dump()
    plan, _ = nightly_plan(c)

    assert [s.node_id for s in plan.routes[0].stops] == ["ПВЗ-D", "ПВЗ-F", "ПВЗ-G", "ПВЗ-D"]
    (stop,) = plan.inserted_stops
    assert (stop.route_id, stop.position, stop.added_min, stop.cost_kop) == ("G2", 3, 4, 6348)
    assert plan.routes[0].stops[stop.position].added
    (assignment,) = plan.assignments
    assert (assignment.variant, assignment.source_node, assignment.qty) == (Variant.VAN, source, 3)
    (leg,) = assignment.legs
    assert (leg.route_id, leg.from_node, leg.to_node) == ("G2", source, "ПВЗ-D")
    assert (leg.depart_min, leg.arrive_min) == (depart, 105)
    assert (plan.total_money_kop, plan.hub_money_kop) == (15948, 18120)
    assert c.model_dump() == original


@pytest.mark.parametrize("qty", [1, 2])
def test_return_visit_requires_savings_in_money(qty):
    plan, _ = nightly_plan(return_city("ПВЗ-G", qty=qty, available=qty))

    assert plan.inserted_stops == []
    (assignment,) = plan.assignments
    assert (assignment.variant, assignment.source_node, assignment.qty) == (Variant.HUB, "HUB", qty)
    assert assignment.legs == []
    assert plan.total_money_kop == plan.hub_money_kop == qty * 6040


def test_return_visit_respects_route_limits():
    limited_time = return_city("ПВЗ-G", qty=3, available=3)
    limited_time.params.max_added_min_abs = 3
    limited_capacity = return_city("ПВЗ-G", qty=3, available=3)
    limited_capacity.routes[0].capacity = 2

    for c in (limited_time, limited_capacity):
        plan, _ = nightly_plan(c)
        assert plan.inserted_stops == []
        assert [(a.variant, a.qty) for a in plan.assignments] == [(Variant.HUB, 3)]


def test_return_visit_position_tracks_later_insertions():
    c = return_city("ПВЗ-G", qty=3, available=3)
    x = next(n for n in c.nodes if n.id == "ПВЗ-X")
    x.x_km, x.y_km = 3, 5.6  # рядом с F, по пути из D
    c.stock.append(StockItem(node_id="ПВЗ-D", sku_id="SKU-002", qty=1))
    c.orders.append(Order(id="new-stop", sku_id="SKU-002", pvz_id="ПВЗ-X"))

    plan, _ = nightly_plan(c)

    assert [(s.node_id, s.position) for s in plan.inserted_stops] == [("ПВЗ-D", 4), ("ПВЗ-X", 1)]
    for inserted in plan.inserted_stops:
        visit = plan.routes[0].stops[inserted.position]
        assert visit.added and visit.node_id == inserted.node_id
    (leg,) = by_order(plan)["return"].legs
    assert leg.arrive_min == 107


def test_return_visit_hub_fills_shortage_directly_for_all_orders():
    c = return_city("ПВЗ-G", qty=5, available=3)
    c.orders.append(Order(id="hub-only", sku_id="SKU-002", qty=2, pvz_id="ПВЗ-X"))
    response = TestClient(app).post(
        "/routing/plan", headers={"X-Role": "logist"}, json=c.model_dump()
    )
    assert response.status_code == 200
    plan = response.json()

    assert len(plan["inserted_stops"]) == 1
    assert [(a["order_id"], a["source_node"], a["qty"]) for a in plan["assignments"]] == [
        ("return", "ПВЗ-G", 3),
        ("return", "HUB", 2),
        ("hub-only", "HUB", 2),
    ]
    for a in plan["assignments"]:
        if a["variant"] == "hub":
            assert a["legs"] == []
        else:
            assert [(leg["from_node"], leg["to_node"]) for leg in a["legs"]] == [("ПВЗ-G", "ПВЗ-D")]
    assert plan["total_money_kop"] == 15948 + 4 * 6040


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


def test_demo_unique_goods_share_return_visit_without_local_fulfillment():
    client = TestClient(app)
    headers = {"X-Role": "logist"}
    response = client.post("/routing/demo/reset", headers=headers)
    assert response.status_code == 200
    response = client.get("/routing/demo", headers=headers)
    assert response.status_code == 200
    c = City.model_validate(response.json()["city"])
    assert [(s.sku_id, s.node_id, s.qty) for s in c.stock if s.sku_id.startswith("UNIQUE-")] == [
        (f"UNIQUE-{node.id}", node.id, 3) for node in c.nodes if node.kind == NodeKind.PVZ
    ]
    c.orders = [
        Order(id="g-to-d", sku_id="UNIQUE-ПВЗ-G", qty=3, pvz_id="ПВЗ-D"),
        Order(id="f-to-d", sku_id="UNIQUE-ПВЗ-F", qty=1, pvz_id="ПВЗ-D"),
    ]
    response = client.post("/routing/plan", headers=headers, json=c.model_dump())
    assert response.status_code == 200
    plan = response.json()
    (return_stop,) = plan["inserted_stops"]
    assert (return_stop["route_id"], return_stop["node_id"], return_stop["position"]) == (
        "G2", "ПВЗ-D", 3
    )
    assert [(a["source_node"], a["variant"], a["qty"]) for a in plan["assignments"]] == [
        ("ПВЗ-G", "van", 3), ("ПВЗ-F", "van", 1),
    ]
    assert all(a["legs"][0]["arrive_min"] == 105 for a in plan["assignments"])


def test_unique_goods_use_cheaper_hub_relay_and_show_real_shortage():
    c = return_city("ПВЗ-G", qty=1, available=1)
    sku = "UNIQUE-ПВЗ-G"
    c.skus.append(Sku(id=sku))
    c.orders[0].sku_id = c.stock[0].sku_id = sku
    c.stock.append(StockItem(node_id="HUB", sku_id=sku, qty=10))
    c.orders.append(Order(id="ordinary", sku_id="SKU-002", qty=1, pvz_id="ПВЗ-X"))

    plan, _ = nightly_plan(c)
    delivered = by_order(plan)["return"]
    assert (delivered.variant, delivered.source_node, delivered.qty) == (Variant.HUB, "ПВЗ-G", 1)
    assert [(leg.from_node, leg.to_node) for leg in delivered.legs] == [
        ("ПВЗ-G", "HUB"), ("HUB", "ПВЗ-D"),
    ]
    assert (delivered.legs[-1].depart_min, delivered.legs[-1].arrive_min) == (1440, 1481)
    assert delivered.hours == 34.68
    assert plan.inserted_stops == []
    assert (plan.total_money_kop, plan.hub_money_kop) == (6750 + 6040, 6040)
    assert plan.unassigned == {}

    c.orders[0].qty = 3
    plan, evaluation = nightly_plan(c)
    assert plan.unassigned == {"return": 2}
    assert {(a.order_id, a.source_node, a.qty) for a in plan.assignments} == {
        ("ordinary", "HUB", 1), ("return", "ПВЗ-G", 1),
    }
    assert evaluation.stock_after[("ПВЗ-G", sku)] == 0
    hub, greedy, nightly = compare(c)
    assert [s.hub_units for s in (hub, greedy, nightly)] == [1, 1, 2]
    assert [s.unassigned_units for s in (hub, greedy, nightly)] == [3, 3, 2]


def test_hub_relay_shares_next_day_departure_capacity_across_skus():
    c = return_city("ПВЗ-G", qty=1, available=1)
    a, b = "UNIQUE-ПВЗ-G", "UNIQUE-ПВЗ-F"
    c.skus.extend([Sku(id=a), Sku(id=b)])
    c.orders[0].sku_id = c.stock[0].sku_id = a
    c.stock.append(StockItem(node_id="ПВЗ-F", sku_id=b, qty=1))
    c.orders.append(Order(id="second", sku_id=b, qty=1, pvz_id="ПВЗ-D"))
    c.routes[0].capacity = 3
    c.routes[0].stops[0].base_drop = 2
    c.params.max_new_stops = 0

    plan, ev = nightly_plan(c)
    (shipment,) = plan.assignments
    assert (shipment.variant, shipment.source_node, shipment.qty) == (Variant.HUB, "ПВЗ-F", 1)
    assert plan.unassigned == {"return": 1}
    assert ev.cap.seg_free["G2", 2] == 2
    assert ev.cap.next_seg_free["G2", -1] == 0
    assert ev.stock_after["ПВЗ-G", a] == 1


def test_unique_goods_without_route_stay_on_the_source_shelf():
    c = return_city("ПВЗ-G", qty=1, available=1)
    sku = "UNIQUE-ПВЗ-G"
    c.skus.append(Sku(id=sku))
    c.orders[0].sku_id = c.stock[0].sku_id = sku
    c.routes = []

    plan, evaluation = nightly_plan(c)
    assert plan.assignments == []
    assert plan.unassigned == {"return": 1}
    assert plan.total_money_kop == plan.hub_money_kop == 0
    assert evaluation.stock_after[("ПВЗ-G", sku)] == 1


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
