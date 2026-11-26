"""Пополнение mesh-полки из хаба популярными для этого ПВЗ товарами (HB1).

Кроме отказов, на полку попадают товары из хаба: те, что часто покупают именно в этой точке.
Спрос точки = продажи товара в этом ПВЗ за 30 дней / 30. Берём товары, которые допущены
в mesh по правилу каталога (категория, маркировка, объём, спрос в городе) и у которых спрос
точки не ниже того же порога модели. Количество — сколько продастся за срок хранения
(14 сут, economics.params.shelf_days), минус то, что уже лежит; всё — в пределах места
на полке и дневного лимита операций ПВЗ. Привоз — обычной утренней газелью из хаба.
"""

import math
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.catalog import service as catalog
from app.modules.catalog.models import Sale
from app.modules.economics.params import DEFAULTS as P
from app.modules.economics.params import kopecks
from app.modules.inventory.models import ON_SHELF, PENDING, Unit
from app.modules.inventory.service import get_pvz, sim_now, used_l
from app.modules.ledger.service import post


def point_demand(session: Session, pvz_id: str) -> dict[str, tuple[int, Decimal]]:
    """sku → (продаж в этом ПВЗ за 30 дней, спрос точки в сутки)."""
    today = catalog._today(session)
    rows = session.execute(
        select(Sale.sku, func.sum(Sale.qty))
        .where(
            Sale.pvz_id == pvz_id,
            Sale.day > today - timedelta(days=catalog.WINDOW_DAYS),
            Sale.day <= today,
        )
        .group_by(Sale.sku)
    ).all()
    return {sku: (int(q), Decimal(int(q)) / catalog.WINDOW_DAYS) for sku, q in rows}


def plan(session: Session, pvz_id: str) -> dict:
    """Что привезти из хаба на полку ПВЗ: товары, штуки и почему. Состояние не меняет."""
    node = get_pvz(session, pvz_id)
    threshold = catalog.threshold_per_day()
    city = {r["sku"]: r for r in catalog.product_rows(session)}
    here = point_demand(session, pvz_id)
    have = dict(
        session.execute(
            select(Unit.sku, func.count())
            .where(Unit.node_id == pvz_id, Unit.status.in_((*ON_SHELF, *PENDING)))
            .group_by(Unit.sku)
        ).all()
    )
    free_l = max(0.0, node.mesh_limit_l - used_l(session, pvz_id))
    slots = node.ops_limit  # приём из газели — те же операции сотрудника
    items, skipped = [], []
    for sku, (sold, per_day) in sorted(here.items(), key=lambda x: (-x[1][1], x[0])):
        row = city.get(sku)
        if row is None:
            continue
        if not row["mesh_eligible"]:
            skipped.append({"sku": sku, "reason": row["reason"]})
            continue
        if per_day < threshold:
            skipped.append({"sku": sku, "reason": f"спрос в этом ПВЗ ниже порога {threshold}/сут"})
            continue
        want = math.ceil(per_day * P.shelf_days) - have.get(sku, 0)
        fit = min(want, int(free_l // row["volume_l"]), slots)
        if fit <= 0:
            if want > 0:
                skipped.append({"sku": sku, "reason": "нет места на полке или лимит операций"})
            continue
        free_l -= fit * row["volume_l"]
        slots -= fit
        items.append(
            {
                "sku": sku,
                "name": row["name"],
                "qty": fit,
                "volume_l": row["volume_l"],
                "sales_30d_here": sold,
                "demand_here_per_day": str(per_day.quantize(Decimal("0.001"))),
                "on_shelf": have.get(sku, 0),
                "reason": (
                    f"в этом ПВЗ берут {sold} шт. за 30 дней — за {P.shelf_days} сут "
                    f"продастся ≈ {math.ceil(per_day * P.shelf_days)} шт."
                ),
            }
        )
    return {
        "pvz_id": pvz_id,
        "threshold_per_day": str(threshold),
        "horizon_days": P.shelf_days,
        "free_l": round(free_l, 3),
        "items": items,
        "skipped": skipped,
    }


def restock(session: Session, pvz_id: str, key: str) -> dict:
    """Привезти из хаба то, что предлагает plan(): единицы ложатся на полку (источник hub).

    Деньги (схема mesh): хаб отбирает и везёт единицу так же, как обычный заказ из хаба
    (hub_order_kop), ПВЗ получает тариф за приём из газели и платит труд сотрудника.
    """
    node = get_pvz(session, pvz_id, lock=True)
    if not node.is_open:
        raise HTTPException(409, f"{pvz_id} закрыт — пополнение невозможно")
    proposal = plan(session, pvz_id)
    now = sim_now(session)
    pvz = f"pvz:{pvz_id}"
    units, txn_ids = [], []
    for item in proposal["items"]:
        for _ in range(item["qty"]):
            unit = Unit(
                barcode=f"H-{uuid4().hex[:10]}",
                sku=item["sku"],
                node_id=pvz_id,
                status="on_shelf",
                source="hub",
                shelved_at=now,
            )
            session.add(unit)
            units.append(unit)
    session.flush()
    n = len(units)
    if n:
        supply = P.hub_order_kop * n
        fee = P.receive_fee_kop * n
        work = kopecks(P.labour(P.receive_min)) * n
        k = f"restock:{pvz_id}:{key}"
        txn_ids = [
            post(session, "mesh", "cost", None, [("mp", -supply), ("ext:hub_ops", supply)],
                 key=f"{k}:hub").id,
            post(session, "mesh", "tariff", None, [("mp", -fee), (pvz, fee)],
                 key=f"{k}:receive").id,
            post(session, "mesh", "cost", None, [(pvz, -work), ("ext:labour", work)],
                 key=f"{k}:labour").id,
        ]  # fmt: skip
    return {
        **proposal,
        "units": [{"unit_id": u.id, "sku": u.sku, "barcode": u.barcode} for u in units],
        "delivered": n,
        "used_l": used_l(session, pvz_id),
        "limit_l": node.mesh_limit_l,
        "txn_ids": txn_ids,
    }


def hub_supplied(session: Session, unit_ids: list[int]) -> bool:
    """Все ли эти единицы — не отказы (из хаба или от продавца): для теневой базовой схемы."""
    if not unit_ids:
        return False
    sources = session.scalars(select(Unit.source).where(Unit.id.in_(unit_ids))).all()
    return all(s != "refusal" for s in sources)

