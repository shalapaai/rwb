"""Нештатные ситуации: ПВЗ закрылся (сценарий 4) и отказ залежался (сценарий 5).

Закрытие ПВЗ. Отмечаем пункт закрытым и пересчитываем только заказы, которые уже
едут туда газелью: routing.daily.close_pvz предлагает выгрузить товар в другой ПВЗ
той же газели (если покупатель согласен и газель его ещё не проехала) или вернуть и
довезти завтра. Заказ → rerouted, закрывшийся ПВЗ платит штраф и доп. затраты,
покупателю и продавцу уходят уведомления.

Тестовые часы. Сдвигаем время и проверяем отказы на полках (решение N0):
на 7-й день — уведомление продавцу, на 14-й — дешевле из «перевезти туда, где
спрос» и «вывезти в хаб». Хранение за дни на полке проводится в момент ухода
единицы с полки — так же, как при отборе под заказ (M1), без двойного начисления.
"""

import math
from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.idempotency import run_once
from app.modules.economics.params import DEFAULTS as P
from app.modules.economics.params import kopecks
from app.modules.inventory.models import Product, Unit
from app.modules.inventory.service import lock_sim_clock, post_storage, sim_now
from app.modules.inventory.shelf_queue import (
    PVZ_TIMEZONE,
    advance_mornings,
    plan_removals,
    waiting_head,
)
from app.modules.ledger.service import post
from app.modules.network.models import Node
from app.modules.ops.models import IdempotencyKey, Incident, Notification
from app.modules.orders.models import Order
from app.modules.orders.service import _chosen, _move, load_day_state
from app.modules.routing import daily
from app.modules.routing.models import Variant
from app.modules.routing.whatif import WhatIfRequest, what_if

# штраф ПВЗ за срыв SLA — 20 ₽ за единицу (docs/02, «Мотивация ПВЗ»), синтетическое допущение
PENALTY_PER_UNIT_KOP = 2000
NOTIFY_DAY = 7  # решение N0: уведомление продавцу
DECIDE_DAY = 14  # решение N0: перевезти туда, где спрос, или вывезти в хаб


def notify(session: Session, role: str, text: str, order_id: int | None = None) -> None:
    session.add(Notification(role=role, text=text, order_id=order_id))


# ---------------------------------------------------------------- сценарий 4


def close_pvz(session: Session, node_id: str, buyer_accepts_alt: bool, now_min: int | None) -> dict:
    node = session.get(Node, node_id)
    if node is None or node.kind != "pvz":
        raise HTTPException(404, f"Нет ПВЗ {node_id}")
    if not node.is_open:
        raise HTTPException(409, f"{node_id} уже закрыт")
    node.is_open = False
    session.flush()

    # заказы в пути именно сюда: товар уже в газели
    in_van = [
        o
        for o in session.scalars(
            select(Order).where(Order.variant == "van", Order.status == "in_transit")
        )
        if (_chosen(o).get("legs") or [{}])[-1].get("to_node") == node_id
    ]
    st = load_day_state(session, in_van)
    decisions = daily.close_pvz(st, node_id, buyer_accepts_alt, PENALTY_PER_UNIT_KOP, now_min)

    incident = Incident(node_id=node_id, kind="pvz_closed", penalty_kop=0)
    session.add(incident)
    session.flush()
    by_id = {str(o.id): o for o in in_van}
    new = {a.order_id: a for a in st.assignments}
    out = []
    for d in decisions:
        order = by_id[d.order_id]
        chosen = next(c for c in d.candidates if c.chosen)
        a = new[d.order_id]
        if chosen.variant == Variant.NEIGHBOR:  # выгрузили в другой ПВЗ той же газели
            order.pvz_id = chosen.source_node
        candidates = [dict(c) for c in order.candidates or []]
        for c in candidates:
            if c.get("chosen"):
                c["legs"] = [leg.model_dump(mode="json") for leg in a.legs]
        candidates.append({"reroute": d.model_dump(mode="json")})  # история: почему так
        order.candidates = candidates
        order.eta_h = a.hours
        _move(order, "rerouted")

        k = f"incident:{incident.id}:order:{order.id}"
        pvz = f"pvz:{node_id}"
        post(
            session,
            "mesh",
            "penalty",
            order.id,
            [(pvz, -d.penalty_kop), ("mp", d.penalty_kop)],
            key=f"{k}:penalty",
        )
        if d.extra_cost_kop:  # доп. затраты несёт виновник — закрывшийся ПВЗ
            post(
                session,
                "mesh",
                "cost",
                order.id,
                [(pvz, -d.extra_cost_kop), ("ext:van", d.extra_cost_kop)],
                key=f"{k}:extra",
            )
        incident.penalty_kop += d.penalty_kop
        buyer_text, seller_text = d.notifications
        notify(session, "buyer", buyer_text, order.id)
        notify(session, "seller", seller_text, order.id)
        out.append(
            {
                "order_id": order.id,
                "status": order.status,
                "version": order.version,
                "pvz_id": order.pvz_id,
                "chosen": chosen.reason,
                "extra_hours": d.extra_hours,
                "extra_cost_kop": d.extra_cost_kop,
                "penalty_kop": d.penalty_kop,
                "candidates": [c.model_dump(mode="json") for c in d.candidates],
                "notifications": d.notifications,
            }
        )
    session.flush()
    return {
        "incident_id": incident.id,
        "node_id": node_id,
        "penalty_kop": incident.penalty_kop,
        "rerouted": out,
    }


# ---------------------------------------------------------------- сценарий 5


def _decide(session: Session, unit: Unit, days: int, k: str, state_box: list) -> dict:
    product = session.get(Product, unit.sku)
    source = unit.node_id
    storage = post_storage(session, source, product.volume_l, days, k)

    # спрос = заказы этого товара в других ПВЗ; больше заказов — первым кандидатом
    demand = session.execute(
        select(Order.pvz_id, func.count())
        .where(Order.sku == unit.sku, Order.pvz_id != source)
        .group_by(Order.pvz_id)
        .order_by(func.count().desc(), Order.pvz_id)
    ).all()
    move = None
    reasons = []
    for dest, n in demand:
        if not state_box:
            state_box.append(load_day_state(session))
        quote = what_if(
            state_box[0], WhatIfRequest(source_pvz=source, sku_id=unit.sku, dest_pvz=dest)
        ).forced
        if quote.variant == Variant.VAN:
            move = (dest, n, quote)
            break
        reasons.append(f"{dest}: {quote.reason}")

    hub_cost = P.hub_return_kop
    move_cost = P.van_space_kop + P.receive_fee_kop if move else None
    if move and move_cost < hub_cost:
        dest, n, quote = move
        post(
            session,
            "mesh",
            "cost",
            None,
            [("mp", -P.van_space_kop), ("ext:van", P.van_space_kop)],
            key=f"{k}:van",
        )
        work = kopecks(P.labour(P.receive_min))
        post(
            session,
            "mesh",
            "tariff",
            None,
            [("mp", -P.receive_fee_kop), (f"pvz:{dest}", P.receive_fee_kop)],
            key=f"{k}:receive",
        )
        post(
            session,
            "mesh",
            "cost",
            None,
            [(f"pvz:{dest}", -work), ("ext:labour", work)],
            key=f"{k}:receive_labour",
        )
        unit.node_id, unit.shelved_at = dest, sim_now(session)  # новая очередь, новый отсчёт
        action = f"перевезти в {dest} газелью {quote.legs[0].route_id} (там {n} заказ.)"
        decision = {"action": "move", "to": dest, "cost_kop": move_cost}
    else:
        post(
            session,
            "mesh",
            "cost",
            None,
            [("mp", -hub_cost), ("ext:hub_ops", hub_cost)],
            key=f"{k}:hub",
        )
        unit.status = "to_hub"
        why = "; ".join(reasons) if reasons else "спроса на этот товар в других ПВЗ нет"
        action = f"вывезти в хаб ({why})"
        decision = {"action": "hub", "to": "HUB", "cost_kop": hub_cost}
    text = f"Отказ {unit.sku} ({unit.barcode}) пролежал {days} сут в {source}: {action}"
    notify(session, "seller", text)
    notify(session, "pvz_operator", text)
    return {
        "unit_id": unit.id,
        "from": source,
        "days": days,
        "storage_kop": storage,
        **decision,
        "text": text,
    }


def _first_time(session: Session, key: str, action) -> dict | None:
    """Выполнить один раз за всё время; при повторе — None, чтобы не попасть в отчёт снова."""
    if session.get(IdempotencyKey, f"aging:{key}") is not None:
        return None
    return run_once(session, "aging", key, action)


def shift_clock(session: Session, days: int, expected_day: date | None = None) -> dict:
    clock = lock_sim_clock(session)
    before = sim_now(session)
    if expected_day is not None and before.astimezone(PVZ_TIMEZONE).date() != expected_day:
        raise HTTPException(409, "День стенда уже изменился. Обновляем полку и задания.")
    clock.offset_days += days
    session.flush()
    now = before + timedelta(days=days)

    advance_mornings(session, now.astimezone(PVZ_TIMEZONE).date())
    notified, decided = [], []
    state_box: list = []  # дневное состояние строим один раз и только если есть спрос
    # Перевозка может изменить как исходную полку, так и полку назначения.
    # Берём блокировки до выбора единиц в одном порядке с приёмом и снятием.
    aged = (
        select(Unit.id)
        .where(
            Unit.status == "on_shelf",
            Unit.reduction_on.is_(None),
            Unit.shelved_at <= now - timedelta(days=DECIDE_DAY),
        )
        .limit(1)
    )
    if session.scalar(aged) is not None:
        session.execute(
            select(Node.id).where(Node.kind == "pvz").order_by(Node.id).with_for_update()
        ).all()
    units = session.scalars(
        select(Unit)
        .where(Unit.status == "on_shelf", Unit.reduction_on.is_(None), Unit.shelved_at.is_not(None))
        .order_by(Unit.shelved_at, Unit.id)
    ).all()
    for u in units:
        age = math.floor((now - u.shelved_at).total_seconds() / 86400)
        cycle = f"{u.id}:{u.shelved_at.isoformat()}"  # после перевозки — новый цикл
        if age >= NOTIFY_DAY:

            def warn(u=u, age=age) -> dict:
                text = (
                    f"Отказ {u.sku} ({u.barcode}) лежит {age} сут в {u.node_id}; "
                    f"на {DECIDE_DAY}-е сутки — перевозка туда, где спрос, или вывоз в хаб"
                )
                notify(session, "seller", text)
                return {"unit_id": u.id, "pvz_id": u.node_id, "days": age, "text": text}

            if (done := _first_time(session, f"{cycle}:d{NOTIFY_DAY}", warn)) is not None:
                notified.append(done)
        if age >= DECIDE_DAY:
            done = _first_time(
                session,
                f"{cycle}:d{DECIDE_DAY}",
                lambda u=u, age=age, cycle=cycle: _decide(
                    session, u, age, f"aging:{cycle}", state_box
                ),
            )
            if done is not None:
                decided.append(done)
    session.flush()
    affected = {d["from"] for d in decided} | {d["to"] for d in decided if d["action"] == "move"}
    for pvz_id in affected:
        node = session.get(Node, pvz_id)
        if node.pending_share_pct or waiting_head(session, pvz_id):
            plan_removals(session, node)
    return {
        "offset_days": clock.offset_days,
        "now": now.isoformat(),
        "notified": notified,
        "decided": decided,
    }
