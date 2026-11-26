"""Приём отказа в ПВЗ и mesh-полка.

Покупатель отказался от товара при получении → сотрудник ставит оценку:
A — не вскрыт → на полку; B — вскрыт, упаковку восстановил → переупаковка → на полку;
C — восстановить нельзя → в хаб. На полку кладём, только если хватает места:
занято + объём ≤ склад × доля mesh (10–50%, выбирает ПВЗ); иначе — в хаб с причиной.
За проверку (при любой оценке) и переупаковку маркетплейс платит ПВЗ тариф, а ПВЗ
несёт реальные затраты — время сотрудника и пакет для B. Все числа — из economics.params
(N8), начисления — через ledger.post, сумма каждой проводки 0.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.economics.params import DEFAULTS as P
from app.modules.economics.params import kopecks
from app.modules.inventory.models import Product, Return, Unit
from app.modules.ledger.service import post
from app.modules.network.models import Node
from app.modules.ops.models import SimClock
from app.modules.orders.models import Order

ON_SHELF = ("on_shelf", "reserved")  # что занимает место на mesh-полке


def sim_now(session: Session) -> datetime:
    """Текущее время с учётом сдвига тестовых часов (сценарий долгого хранения)."""
    clock = session.get(SimClock, 1)
    return datetime.now(UTC) + timedelta(days=clock.offset_days if clock else 0)


def get_pvz(session: Session, pvz_id: str, lock: bool = False) -> Node:
    q = select(Node).where(Node.id == pvz_id, Node.kind == "pvz")
    if lock:
        q = q.with_for_update()  # параллельные приёмы в один ПВЗ не переполнят полку
    node = session.scalar(q)
    if node is None:
        raise HTTPException(404, f"Нет ПВЗ {pvz_id}")
    return node


def used_l(session: Session, pvz_id: str) -> float:
    q = (
        select(func.coalesce(func.sum(Product.volume_l), 0.0))
        .select_from(Unit)
        .join(Product, Product.sku == Unit.sku)
        .where(Unit.node_id == pvz_id, Unit.status.in_(ON_SHELF))
    )
    return float(session.scalar(q))


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
    if grade == "C":
        decision, reason = "hub", "оценка C: восстановить нельзя"
    elif not product.mesh_allowed:
        decision, reason = "hub", "товар не допущен на mesh-полку"
    elif used + product.volume_l > limit:
        decision, reason = (
            "hub",
            (f"полка заполнена: занято {used:g} из {limit:g} л, нужно {product.volume_l:g} л"),
        )
    else:
        decision = "repack" if grade == "B" else "shelf"
        reason = "переупакован, на полку" if grade == "B" else "не вскрыт, на полку"

    on_shelf = decision != "hub"
    unit = Unit(
        barcode=barcode or f"R-{uuid4().hex[:10]}",
        sku=sku,
        node_id=pvz_id,
        status="on_shelf" if on_shelf else "to_hub",
        grade=grade,
        shelved_at=sim_now(session) if on_shelf else None,
    )
    session.add(unit)
    session.flush()
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

    return {
        "unit_id": unit.id,
        "barcode": unit.barcode,
        "placement": "shelf" if on_shelf else "hub",
        "decision": decision,
        "reason": reason,
        "used_l": used + (product.volume_l if on_shelf else 0.0),
        "limit_l": limit,
        "txn_ids": txn_ids,
    }


def stock(session: Session, pvz_id: str) -> dict:
    node = get_pvz(session, pvz_id)
    now = sim_now(session)
    rows = session.execute(
        select(Unit, Product)
        .join(Product, Product.sku == Unit.sku)
        .where(Unit.node_id == pvz_id, Unit.status.in_(ON_SHELF))
        .order_by(Unit.shelved_at)  # очередь отказов: сверху самые старые
    ).all()
    units = [
        {
            "unit_id": u.id,
            "barcode": u.barcode,
            "sku": u.sku,
            "status": u.status,
            "grade": u.grade,
            "volume_l": p.volume_l,
            "shelved_at": u.shelved_at.isoformat() if u.shelved_at else None,
            "age_days": (now - u.shelved_at).days if u.shelved_at else 0,
        }
        for u, p in rows
    ]
    return {
        "pvz_id": pvz_id,
        "mesh_share_pct": node.mesh_share_pct,
        "used_l": sum(x["volume_l"] for x in units),
        "limit_l": node.mesh_limit_l,
        "units": units,
    }
