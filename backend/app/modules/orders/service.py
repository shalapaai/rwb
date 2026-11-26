"""Сквозной заказ: выбор варианта → резерв → отбор → газель → приём → выдача.

1. Город для алгоритма собирается из БД: узлы, рейсы газелей, отказы на полках.
   Места в газелях, уже занятые активными заказами, вычитаются.
2. routing.daily.choose выбирает вариант: своя полка → соседний ПВЗ → газель → хаб,
   с ценой, сроком и причиной для каждого кандидата — всё сохраняется в заказе.
3. Резервируется самая старая единица на полке источника (очередь отказов, FIFO);
   второй активный резерв той же единицы не пустит индекс в БД (инвариант 3).
4. Статус меняется только по таблице TRANSITIONS и только с верной версией (инвариант 4).
5. Каждый шаг проводит деньги через ledger.post; при создании пишутся и теневые
   проводки scheme='base' — как тот же заказ прошёл бы через хаб, для сравнения.
"""

import math
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.modules.economics.params import DEFAULTS as P
from app.modules.economics.params import kopecks, routing_params
from app.modules.inventory.models import Product, Unit
from app.modules.inventory.service import sim_now, used_l
from app.modules.ledger.models import LedgerEntry, LedgerTxn
from app.modules.ledger.service import post
from app.modules.network.models import Node, VanRoute, VanStop
from app.modules.orders import pricing
from app.modules.orders.models import Order, Reservation
from app.modules.routing import models as rm
from app.modules.routing.daily import choose, day_state
from app.modules.routing.network import Capacity

# какой статус в какой можно перевести; остальное — 409
TRANSITIONS: dict[str, set[str]] = {
    "created": {"reserved", "in_transit", "cancelled"},  # in_transit — заказ из хаба
    "reserved": {"picked", "ready", "cancelled", "rerouted"},  # ready — своя / соседняя полка
    "picked": {"in_transit", "cancelled", "rerouted"},
    "in_transit": {"ready", "rerouted"},
    "ready": {"issued", "returned"},
    "rerouted": {"in_transit", "ready", "cancelled"},
    "issued": {"returned"},
    "cancelled": set(),
    "returned": set(),
}
ACTIVE_VAN = ("reserved", "picked", "in_transit")


def _conflict(msg: str) -> HTTPException:
    return HTTPException(409, msg)


# ---------------------------------------------------------------- город из БД


def build_city(session: Session) -> rm.City:
    params = routing_params()
    nodes = []
    for n in session.scalars(select(Node).order_by(Node.id)):
        free = max(0.0, n.mesh_limit_l - used_l(session, n.id)) if n.kind == "pvz" else 0.0
        nodes.append(
            rm.Node(
                id=n.id, kind=n.kind, x_km=n.x_km, y_km=n.y_km, is_open=n.is_open, shelf_free_l=free
            )
        )
    routes = []
    for r in session.scalars(select(VanRoute).order_by(VanRoute.id)):
        stops = session.scalars(
            select(VanStop).where(VanStop.route_id == r.id).order_by(VanStop.seq)
        )
        routes.append(
            rm.VanRoute(
                id=r.id,
                depot_id=r.depot_id,
                capacity=r.capacity,
                stops=[
                    rm.Stop(
                        node_id=s.node_id,
                        eta_min=s.eta_min,
                        service_min=s.service_min,
                        base_drop=s.base_drop,
                        added=s.added,
                    )
                    for s in stops
                ],
            )
        )
    skus = [
        rm.Sku(id=p.sku, volume_l=p.volume_l, price_kop=p.price_kop)
        for p in session.scalars(select(Product))
    ]
    counts: dict[tuple[str, str], int] = {}
    for u in session.scalars(select(Unit).where(Unit.status == "on_shelf")):
        if u.node_id:
            counts[(u.node_id, u.sku)] = counts.get((u.node_id, u.sku), 0) + 1
    stock = [rm.StockItem(node_id=n, sku_id=s, qty=q) for (n, s), q in counts.items()]
    return rm.City(nodes=nodes, routes=routes, skus=skus, stock=stock, params=params)


def _chosen(order: Order) -> dict:
    return next((c for c in order.candidates or [] if c.get("chosen")), {})


def _busy_capacity(session: Session, city: rm.City) -> Capacity:
    """Места в газелях и операции ПВЗ минус то, что уже заняли активные заказы."""
    nodes = {n.id: n for n in city.nodes}
    cap = Capacity.from_routes(city.routes, nodes, city.params)
    routes = {r.id: r for r in city.routes}
    active = session.scalars(
        select(Order).where(Order.variant == "van", Order.status.in_(ACTIVE_VAN))
    )
    for o in active:
        legs = _chosen(o).get("legs") or []
        for leg in legs:
            r = routes.get(leg["route_id"])
            idx = {(s.node_id, s.eta_min): k for k, s in enumerate(r.stops)} if r else {}
            k_from = idx.get((leg["from_node"], leg["depart_min"]))
            k_to = idx.get((leg["to_node"], leg["arrive_min"]))
            if k_from is None or k_to is None:
                continue  # рейс изменился — этот заказ уже не занимает старые места
            for j in range(k_from, k_to):
                cap.seg_free[(r.id, j)] -= o.qty
        if legs:
            cap.out_left[legs[0]["from_node"]] = cap.out_left.get(legs[0]["from_node"], 0) - o.qty
            cap.in_left[legs[-1]["to_node"]] = cap.in_left.get(legs[-1]["to_node"], 0) - o.qty
    return cap


# ---------------------------------------------------------------- создание заказа


def create_order(
    session: Session, sku: str, pvz_id: str, accepts_neighbor: bool, qty: int = 1
) -> Order:
    product = session.get(Product, sku)
    if product is None:
        raise HTTPException(404, f"Нет товара {sku}")
    pvz = session.get(Node, pvz_id)
    if pvz is None or pvz.kind != "pvz":
        raise HTTPException(404, f"Нет ПВЗ {pvz_id}")

    city = build_city(session)
    stock = {(s.node_id, s.sku_id): s.qty for s in city.stock}
    st = day_state(city, city.routes, _busy_capacity(session, city), stock, [])
    decision = choose(
        st,
        rm.Order(id="new", sku_id=sku, qty=qty, pvz_id=pvz_id, accepts_neighbor=accepts_neighbor),
    )
    a = decision.assignment
    if a is None:
        raise _conflict(decision.candidates[0].reason)  # например, ПВЗ выдачи закрыт

    candidates = [c.model_dump(mode="json") for c in decision.candidates]
    for c in candidates:
        if c["chosen"]:
            c["legs"] = [leg.model_dump(mode="json") for leg in a.legs]
    order = Order(
        sku=sku,
        qty=qty,
        pvz_id=pvz_id,
        accepts_neighbor=accepts_neighbor,
        variant=a.variant.value,
        source_node=a.source_node,
        cost_kop=a.money_kop,
        eta_h=a.hours,
        candidates=candidates,
    )
    session.add(order)
    session.flush()

    if a.variant == rm.Variant.HUB:
        order.status = "in_transit"  # везёт хаб (заглушка), mesh-запаса не тратим
        post(
            session,
            "mesh",
            "cost",
            order.id,
            [("mp", -P.hub_order_kop), ("ext:hub_ops", P.hub_order_kop)],
            key=f"order:{order.id}:hub",
        )
    else:
        _reserve(session, order, a.source_node, qty)
        order.status = "reserved"
    _post_base(session, order, product, local=a.variant != rm.Variant.HUB)
    session.flush()
    return order


def _reserve(session: Session, order: Order, node_id: str, qty: int) -> None:
    units = session.scalars(
        select(Unit)
        .where(Unit.node_id == node_id, Unit.sku == order.sku, Unit.status == "on_shelf")
        .order_by(Unit.shelved_at, Unit.id)  # очередь отказов: старые первыми
        .limit(qty)
        .with_for_update(skip_locked=True)  # параллельный заказ возьмёт следующие
    ).all()
    if len(units) < qty:
        raise _conflict("Пока выбирали вариант, единицу забрал другой заказ — повторите")
    try:
        for u in units:
            u.status = "reserved"
            session.add(Reservation(unit_id=u.id, order_id=order.id))
        session.flush()
    except (IntegrityError, StaleDataError) as exc:
        raise _conflict("Единица уже зарезервирована другим заказом") from exc


def _post_base(session: Session, order: Order, product: Product, local: bool) -> None:
    """Теневые проводки: тот же заказ в базовой схеме через хаб."""
    k = f"order:{order.id}:base"
    hub_cost = P.hub_order_kop + (P.hub_return_kop if local else 0)  # отказ ехал бы в хаб
    legs = [("mp", -hub_cost), ("ext:hub_ops", hub_cost)]
    post(session, "base", "cost", order.id, legs, key=f"{k}:hub")
    _post_sale(session, order, product, "base", pricing.SELLER_LOGISTICS_BASE_KOP, order.pvz_id, k)


def _post_sale(session, order, product, scheme, logistics_kop, issue_node, k) -> None:
    price = product.price_kop * order.qty
    payout = price - pricing.pct(price, pricing.COMMISSION_PCT) - logistics_kop * order.qty
    fee = pricing.pct(price, pricing.ISSUE_FEE_PCT)
    post(session, scheme, "sale", order.id, [("ext:buyer", -price), ("mp", price)], key=f"{k}:paid")
    post(
        session,
        scheme,
        "sale",
        order.id,
        [("mp", -payout), (f"seller:{product.seller_id}", payout)],
        key=f"{k}:payout",
    )
    post(
        session,
        scheme,
        "tariff",
        order.id,
        [("mp", -fee), (f"pvz:{issue_node}", fee)],
        key=f"{k}:issue_fee",
    )


# ---------------------------------------------------------------- действия ПВЗ


def _load(session: Session, order_id: int, version: int) -> Order:
    order = session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(404, f"Нет заказа {order_id}")
    if order.version != version:
        raise _conflict(f"Заказ уже изменён (версия {order.version}, у вас {version}) — обновите")
    return order


def _move(order: Order, to: str) -> None:
    if to not in TRANSITIONS[order.status]:
        raise _conflict(f"Нельзя перевести заказ из «{order.status}» в «{to}»")
    order.status = to


def _units(session: Session, order: Order) -> list[Unit]:
    return list(
        session.scalars(
            select(Unit)
            .join(Reservation, Reservation.unit_id == Unit.id)
            .where(Reservation.order_id == order.id, Reservation.active)
        )
    )


def issue_node(order: Order) -> str:
    """Где покупатель забирает: соседний ПВЗ — у источника, иначе в своём ПВЗ."""
    return order.source_node if order.variant == "neighbor" else order.pvz_id


def _labour(minutes) -> int:
    return kopecks(P.labour(minutes))


def act(session: Session, order_id: int, action: str, version: int) -> Order:
    order = _load(session, order_id, version)
    k = f"order:{order.id}:{action}"
    units = _units(session, order)
    if action == "pick":
        if order.variant not in ("own", "neighbor", "van"):
            raise _conflict("Отбирать с полки нечего: заказ идёт из хаба")
        # своя / соседняя полка — сразу в ячейку выдачи; к газели — ждёт погрузки
        _move(order, "picked" if order.variant == "van" else "ready")
        _post_storage(session, order, units, k)
        src = f"pvz:{order.source_node}"
        post(
            session,
            "mesh",
            "tariff",
            order.id,
            [("mp", -P.pick_fee_kop * order.qty), (src, P.pick_fee_kop * order.qty)],
            key=f"{k}:fee",
        )
        work = _labour(P.pick_min) * order.qty
        post(
            session,
            "mesh",
            "cost",
            order.id,
            [(src, -work), ("ext:labour", work)],
            key=f"{k}:labour",
        )
        if order.variant != "van":
            for u in units:
                u.status = "ready"
    elif action == "load":
        if order.variant != "van":
            raise _conflict("Грузить в газель можно только заказ с вариантом «газель»")
        _move(order, "in_transit")
        for u in units:
            u.status, u.node_id = "in_transit", None
        space = P.van_space_kop * order.qty  # место в попутной газели
        post(
            session, "mesh", "cost", order.id, [("mp", -space), ("ext:van", space)], key=f"{k}:van"
        )
    elif action == "receive":
        _move(order, "ready")
        for u in units:
            u.status, u.node_id = "ready", order.pvz_id
        if order.variant == "van":
            dst = f"pvz:{order.pvz_id}"
            fee, work = P.receive_fee_kop * order.qty, _labour(P.receive_min) * order.qty
            post(session, "mesh", "tariff", order.id, [("mp", -fee), (dst, fee)], key=f"{k}:fee")
            post(
                session,
                "mesh",
                "cost",
                order.id,
                [(dst, -work), ("ext:labour", work)],
                key=f"{k}:labour",
            )
    elif action == "issue":
        _move(order, "issued")
        for u in units:
            u.status = "issued"
        for r in session.scalars(select(Reservation).where(Reservation.order_id == order.id)):
            r.active = False
        product = session.get(Product, order.sku)
        local = order.variant != "hub"
        logistics = (
            pricing.SELLER_LOGISTICS_MESH_KOP if local else pricing.SELLER_LOGISTICS_BASE_KOP
        )
        _post_sale(session, order, product, "mesh", logistics, issue_node(order), k)
        if local and P.it_kop:
            # Сканирование и ИТ оплачивает МП один раз за выданный местный заказ.
            post(
                session,
                "mesh",
                "cost",
                order.id,
                [("mp", -P.it_kop), ("ext:it", P.it_kop)],
                key=f"{k}:it",
            )
    elif action == "cancel":
        _move(order, "cancelled")
        for u in units:
            u.status = "on_shelf"
        for r in session.scalars(select(Reservation).where(Reservation.order_id == order.id)):
            r.active = False
        _reverse(session, order, "base", k)  # заказа не было — тень тоже снимаем
    else:
        raise HTTPException(404, f"Нет действия {action}")
    try:
        session.flush()
    except StaleDataError as exc:
        raise _conflict("Заказ или единицу одновременно изменили — обновите") from exc
    return order


def _post_storage(session: Session, order: Order, units: list[Unit], k: str) -> None:
    """Хранение отказа на полке — при отборе, за фактические сутки (минимум одни)."""
    now = sim_now(session)
    product = session.get(Product, order.sku)
    days = sum(
        max(1, math.ceil((now - u.shelved_at).total_seconds() / 86400))
        for u in units
        if u.shelved_at
    )
    if not days:
        return
    litre_days = Decimal(str(product.volume_l)) * days
    rate = P.space_kop_l_day + P.shelf_labour_kop_l_day + P.shelf_profit_kop_l_day
    fee = kopecks(rate * litre_days)
    rent = kopecks(P.space_kop_l_day * litre_days)
    work = kopecks(P.shelf_labour_kop_l_day * litre_days)
    src = f"pvz:{order.source_node}"
    post(session, "mesh", "tariff", order.id, [("mp", -fee), (src, fee)], key=f"{k}:storage")
    if rent + work:
        legs = [(src, -(rent + work))] + [
            (a, v) for a, v in (("ext:rent", rent), ("ext:labour", work)) if v
        ]
        post(session, "mesh", "cost", order.id, legs, key=f"{k}:storage_cost")


def _reverse(session: Session, order: Order, scheme: str, k: str) -> None:
    """Сторно: новая проводка с обратными знаками, старые записи не трогаем."""
    txns = session.scalars(
        select(LedgerTxn).where(LedgerTxn.order_id == order.id, LedgerTxn.scheme == scheme)
    ).all()
    for t in txns:
        legs = [
            (e.account, -e.amount_kop)
            for e in session.scalars(select(LedgerEntry).where(LedgerEntry.txn_id == t.id))
        ]
        post(session, scheme, "return", order.id, legs, key=f"{k}:reverse:{t.id}")


# ---------------------------------------------------------------- задания ПВЗ


def tasks(session: Session, pvz_id: str) -> dict:
    if session.get(Node, pvz_id) is None:
        raise HTTPException(404, f"Нет ПВЗ {pvz_id}")
    orders = session.scalars(
        select(Order).where(Order.status.in_(("reserved", "picked", "in_transit", "ready")))
    ).all()

    def row(o: Order) -> dict:
        legs = _chosen(o).get("legs") or []
        return {
            "order_id": o.id,
            "version": o.version,
            "sku": o.sku,
            "qty": o.qty,
            "status": o.status,
            "variant": o.variant,
            "source_node": o.source_node,
            "pvz_id": o.pvz_id,
            "van": legs[0]["route_id"] if legs else None,
            "van_eta_min": legs[0]["depart_min"] if legs else None,
        }

    return {
        "pvz_id": pvz_id,
        "to_pick": [row(o) for o in orders if o.status == "reserved" and o.source_node == pvz_id],
        "to_load": [row(o) for o in orders if o.status == "picked" and o.source_node == pvz_id],
        "to_receive": [row(o) for o in orders if o.status == "in_transit" and o.pvz_id == pvz_id],
        "to_issue": [row(o) for o in orders if o.status == "ready" and issue_node(o) == pvz_id],
    }
