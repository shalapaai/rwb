"""Приём отказа в ПВЗ и mesh-полка.

Покупатель отказался от товара при получении → сотрудник ставит оценку:
A — не вскрыт → на полку; B — вскрыт, упаковку восстановил → переупаковка → на полку;
C — восстановить нельзя → в хаб. Если для пригодного отказа не хватает места,
оператор снимает старые единицы по очереди и подтверждает размещение новой.
За проверку (при любой оценке) и переупаковку маркетплейс платит ПВЗ тариф, а ПВЗ
несёт реальные затраты — время сотрудника и пакет для B. Все числа — из economics.params
(N8), начисления — через ledger.post, сумма каждой проводки 0.
"""

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.modules.economics.params import DEFAULTS as P
from app.modules.economics.params import kopecks
from app.modules.inventory.models import (
    MAX_MESH_UNIT_L,
    ON_SHELF,
    PENDING,
    Product,
    ReductionDispatch,
    Return,
    ShelfWriteOff,
    Unit,
)
from app.modules.inventory.shelf_queue import (
    PVZ_TIMEZONE,
    estimated_days,
    fits,
    plan_removals,
    reduction_plan,
    used_l,
    waiting_head,
)
from app.modules.ledger.service import post
from app.modules.network.models import Node
from app.modules.ops.models import SimClock
from app.modules.orders.models import Order


def sim_now(session: Session) -> datetime:
    """Время стенда остаётся фиксированным между ручными переходами."""
    clock = session.get_one(SimClock, 1)
    base = clock.base_at
    if base.tzinfo is None:  # SQLite в тестах сети возвращает метки UTC без часового пояса.
        base = base.replace(tzinfo=UTC)
    return base + timedelta(days=clock.offset_days)


def lock_sim_clock(session: Session) -> SimClock:
    """Сохранение цели и переход на новое утро должны видеть друг друга в порядке записи."""
    session.execute(insert(SimClock).values(id=1, offset_days=0).on_conflict_do_nothing())
    return session.scalars(
        select(SimClock)
        .where(SimClock.id == 1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def get_pvz(session: Session, pvz_id: str, lock: bool = False) -> Node:
    q = select(Node).where(Node.id == pvz_id, Node.kind == "pvz")
    if lock:
        q = q.with_for_update(key_share=True)  # параллельные приёмы в один ПВЗ не переполнят полку
    node = session.scalar(q)
    if node is None:
        raise HTTPException(404, f"Нет ПВЗ {pvz_id}")
    return node


def volume_reason(volume_l: float, limit_l: float) -> str:
    if volume_l > MAX_MESH_UNIT_L:
        return f"Превышен объём товара: {volume_l:g} л при лимите {MAX_MESH_UNIT_L} л на единицу"
    return f"Объём товара {volume_l:g} л превышает лимит всей полки {limit_l:g} л"


def accept_refusal(
    session: Session,
    pvz_id: str,
    sku: str,
    grade: str,
    key: str,
    order_id: int | None = None,
    barcode: str | None = None,
    comment: str = "",
) -> dict:
    node = get_pvz(session, pvz_id, lock=True)
    if not node.is_open:
        raise HTTPException(409, f"{pvz_id} закрыт — приём невозможен")
    product = session.get(Product, sku)
    if product is None:
        raise HTTPException(404, f"Нет товара {sku}")
    if order_id is not None and session.get(Order, order_id) is None:
        raise HTTPException(404, f"Нет заказа {order_id}")

    used, limit = used_l(session, pvz_id), node.mesh_limit_l
    now = sim_now(session)
    status = "to_hub"
    category = None
    if product.volume_l > MAX_MESH_UNIT_L:
        category = "volume"
        decision, reason = "hub", volume_reason(product.volume_l, limit)
    elif grade == "C":
        category = "packaging"
        decision, reason = "hub", "Упаковку нельзя восстановить (оценка C)"
    elif not product.mesh_allowed:
        decision, reason = "hub", "товар не допущен на mesh-полку"
    elif product.volume_l > limit:
        category = "volume"
        decision, reason = "hub", volume_reason(product.volume_l, limit)
    else:
        decision = "repack" if grade == "B" else "shelf"
        if fits(used, product.volume_l, limit) and not waiting_head(session, node.id):
            status = "on_shelf"
            reason = "переупакован, на полку" if grade == "B" else "не вскрыт, на полку"
        else:
            status = "awaiting_shelf"
            reason = "Товар принят в очередь. Разместите его по заданию, когда освободится место."

    unit = Unit(
        barcode=barcode or f"R-{uuid4().hex[:10]}",
        sku=sku,
        node_id=pvz_id,
        status=status,
        grade=grade,
        shelved_at=now if status == "on_shelf" else None,
    )
    session.add(unit)
    session.flush()
    if category:
        session.add(ShelfWriteOff(
            unit_id=unit.id, pvz_id=pvz_id, day=now.astimezone(PVZ_TIMEZONE).date(),
            category=category, reason=reason,
        ))
    if status in PENDING or node.pending_share_pct:
        plan_removals(session, node)
    session.add(
        Return(
            unit_id=unit.id,
            order_id=order_id,
            pvz_id=pvz_id,
            kind="refusal",
            grade=grade,
            decision=decision,
            reason=(reason + (f"; {comment}" if comment else "")),
        )
    )

    pvz = f"pvz:{pvz_id}"
    # (операция, тариф МП → ПВЗ, реальные затраты ПВЗ во внешний мир)
    ops = [("check", P.inspection_fee_kop, [("ext:labour", kopecks(P.labour(P.inspection_min)))])]
    if decision == "repack":
        repack_costs = [
            ("ext:labour", kopecks(P.labour(P.repack_min))),
            ("ext:materials", P.pack_kop),  # пакет
        ]
        ops.append(("repack", P.repack_fee_kop, repack_costs))
    txn_ids = []
    for op, fee, costs in ops:
        # ключи проводок выводятся из ключа запроса — повтор их тоже не задвоит
        paid = post(
            session,
            "mesh",
            "tariff",
            order_id,
            [("mp", -fee), (pvz, fee)],
            key=f"refusal:{key}:{op}:tariff",
        )
        spent = sum(amount for _, amount in costs)
        cost = post(
            session,
            "mesh",
            "cost",
            order_id,
            [(pvz, -spent), *costs],
            key=f"refusal:{key}:{op}:cost",
        )
        txn_ids += [paid.id, cost.id]

    used = used_l(session, pvz_id)
    return {
        "unit_id": unit.id,
        "barcode": unit.barcode,
        "placement": {
            "on_shelf": "shelf",
            "awaiting_shelf": "queue",
            "awaiting_return": "return_queue",
            "to_hub": "hub",
        }[status],
        "decision": decision,
        "reason": reason,
        "used_l": used,
        "limit_l": node.mesh_limit_l,
        "txn_ids": txn_ids,
    }


def stock(session: Session, pvz_id: str) -> dict:
    node = get_pvz(session, pvz_id)
    now = sim_now(session)
    rows = session.execute(
        select(Unit, Product)
        .join(Product, Product.sku == Unit.sku)
        .where(Unit.node_id == pvz_id, Unit.status.in_((*ON_SHELF, *PENDING)))
        .order_by(Unit.shelved_at.nulls_last(), Unit.id)  # очередь отказов: сверху самые старые
    ).all()
    units = [
        {
            "unit_id": u.id,
            "barcode": u.barcode,
            "sku": u.sku,
            "status": "reduction_planned"
            if u.status == "on_shelf" and u.reduction_on is not None
            else u.status,
            "grade": u.grade,
            "volume_l": p.volume_l,
            "shelved_at": u.shelved_at.isoformat() if u.shelved_at else None,
            "age_days": (now - u.shelved_at).days if u.shelved_at else 0,
        }
        for u, p in rows
    ]
    used = float(sum(Decimal(str(p.volume_l)) for u, p in rows if u.status in ON_SHELF))
    day = now.astimezone(PVZ_TIMEZONE).date()
    return {
        "pvz_id": pvz_id,
        "simulation_day": day,
        "writeoffs_today": session.execute(
            select(
                ShelfWriteOff.unit_id, Unit.barcode, Product.sku, Product.name, Product.volume_l,
                ShelfWriteOff.category, ShelfWriteOff.reason,
            )
            .select_from(ShelfWriteOff)
            .join(Unit, Unit.id == ShelfWriteOff.unit_id)
            .join(Product, Product.sku == Unit.sku)
            .where(ShelfWriteOff.pvz_id == pvz_id, ShelfWriteOff.day == day)
            .order_by(ShelfWriteOff.unit_id.desc())
        ).mappings().all(),
        "mesh_share_pct": node.active_share_pct,
        "estimated_days": estimated_days(session, node),
        "pending_share_pct": node.pending_share_pct,
        "used_l": used,
        "limit_l": node.mesh_limit_l,
        "units": units,
        "products": session.execute(
            select(Product.sku, Product.name, Product.volume_l).order_by(
                Product.volume_l.desc(), Product.sku
            )
        )
        .mappings()
        .all(),
    }


def post_storage(
    session: Session, pvz_id: str, volume_l: float, days: int, k: str, order_id: int | None = None
) -> int:
    """Начислить хранение при физическом уходе товара с полки."""
    litre_days = Decimal(str(volume_l)) * days
    rate = P.space_kop_l_day + P.shelf_labour_kop_l_day + P.shelf_profit_kop_l_day
    fee = kopecks(rate * litre_days)
    rent = kopecks(P.space_kop_l_day * litre_days)
    work = kopecks(P.shelf_labour_kop_l_day * litre_days)
    src = f"pvz:{pvz_id}"
    if fee:
        post(session, "mesh", "tariff", order_id, [("mp", -fee), (src, fee)], key=f"{k}:storage")
    if rent + work:
        legs = [(src, -(rent + work))] + [
            (a, v) for a, v in (("ext:rent", rent), ("ext:labour", work)) if v
        ]
        post(session, "mesh", "cost", order_id, legs, key=f"{k}:storage_cost")
    return fee


def complete_shelf_task(
    session: Session, pvz_id: str, unit_id: int, action: str, version: int
) -> dict:
    node = get_pvz(session, pvz_id, lock=True)
    unit = session.scalar(
        select(Unit).where(Unit.id == unit_id, Unit.node_id == pvz_id).with_for_update()
    )
    if unit is None:
        raise HTTPException(404, "Товар не найден в этом ПВЗ")
    expected = {
        "reduce": "on_shelf",
        "remove": "remove_pending",
        "return": "awaiting_return",
        "shelve": "awaiting_shelf",
    }[action]
    if unit.version != version or unit.status != expected:
        raise HTTPException(409, "Задание уже изменилось. Обновите список заданий.")

    now = sim_now(session)
    if action == "return":
        volume = session.get(Product, unit.sku).volume_l
        session.add(ShelfWriteOff(
            unit_id=unit.id, pvz_id=pvz_id, day=now.astimezone(PVZ_TIMEZONE).date(),
            category="volume", reason=volume_reason(volume, node.mesh_limit_l),
        ))
    if action == "reduce":
        if unit.reduction_on is None:
            raise HTTPException(409, "Задание на возврат для этой единицы отсутствует")
        summary, tasks = reduction_plan(session, node, now)
        if not tasks or tasks[0]["unit_id"] != unit_id:
            raise HTTPException(409, "План изменился. Начните со старейшего товара в очереди.")
        head = tasks[0]
        if head["blocked_reason"]:
            raise HTTPException(409, head["blocked_reason"])
        session.add(
            ReductionDispatch(
                unit_id=unit.id,
                pvz_id=pvz_id,
                day=summary["day"],
                volume_l=Decimal(str(head["volume_l"])),
            )
        )
    if action in ("remove", "return", "reduce"):
        unit.reduction_on = None
        unit.status = "to_hub"
        key = f"shelf-return:{unit.id}:{version}"
        if unit.shelved_at:
            days = max(1, math.ceil((now - unit.shelved_at).total_seconds() / 86400))
            post_storage(session, pvz_id, session.get(Product, unit.sku).volume_l, days, key)
        cost = P.hub_return_kop
        if cost:
            post(session, "mesh", "cost", None, [("mp", -cost), ("ext:hub_ops", cost)], key=key)
    else:
        head = waiting_head(session, pvz_id)
        if head is None or head[0].id != unit_id:
            raise HTTPException(409, "Сначала разместите первый товар в очереди")
        if not fits(used_l(session, pvz_id), head.volume_l, node.mesh_limit_l):
            raise HTTPException(409, "Для размещения ещё недостаточно свободного места")
        unit.status, unit.shelved_at = "on_shelf", now
    session.flush()
    plan_removals(session, node)
    return {"unit_id": unit_id, "status": unit.status}


def change_share(session: Session, pvz_id: str, share: int) -> dict:
    lock_sim_clock(session)
    node = get_pvz(session, pvz_id, lock=True)
    current = Decimal(str(node.mesh_limit_l))
    requested = Decimal(str(node.storage_total_l)) * share / 100
    if requested < current:
        node.pending_share_pct = share
        if node.next_reduction_day is None:
            node.next_reduction_day = sim_now(session).astimezone(PVZ_TIMEZONE).date() + timedelta(
                days=1
            )
    else:
        node.mesh_share_pct = share
        node.active_limit_l = None
        node.pending_share_pct = None
        node.next_reduction_day = None
    plan_removals(session, node, share_changed=True)
    used = used_l(session, pvz_id)
    return {
        "pvz_id": pvz_id,
        "mesh_share_pct": node.active_share_pct,
        "estimated_days": estimated_days(session, node),
        "pending_share_pct": node.pending_share_pct,
        "used_l": used,
        "limit_l": node.mesh_limit_l,
        "over_limit": used > node.mesh_limit_l,
    }
