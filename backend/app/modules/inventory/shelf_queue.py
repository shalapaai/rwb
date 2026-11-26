"""Очередь размещения отказов и освобождение места на mesh-полке.

Задание задаётся состоянием физической единицы. Ожидающие занимают место
только после подтверждения размещения, а снятие предлагается по возрасту.
Изменения очереди одного ПВЗ сериализуются блокировкой строки пункта.
"""

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.orm import Session

from app.modules.inventory.models import ON_SHELF, PENDING, Product, ReductionDispatch, Unit
from app.modules.network.models import Node

DAILY_REDUCTION_L = Decimal(10)
PVZ_TIMEZONE = ZoneInfo("Europe/Moscow")


def _rows(session: Session, pvz_id: str, statuses: tuple[str, ...]):
    return (
        select(Unit, Product.volume_l)
        .join(Product, Product.sku == Unit.sku)
        .where(Unit.node_id == pvz_id, Unit.status.in_(statuses))
    )


def used_l(session: Session, pvz_id: str) -> float:
    query = (
        select(func.coalesce(func.sum(cast(Product.volume_l, Numeric)), 0))
        .select_from(Unit)
        .join(Product, Product.sku == Unit.sku)
        .where(Unit.node_id == pvz_id, Unit.status.in_(ON_SHELF))
    )
    return float(session.scalar(query))


def waiting_head(session: Session, pvz_id: str):
    # Приём в ПВЗ сериализован, поэтому id задаёт порядок регистрации даже при сдвиге часов.
    return session.execute(
        _rows(session, pvz_id, ("awaiting_shelf",)).order_by(Unit.id).limit(1)
    ).first()


def fits(used: float, volume: float, limit: float) -> bool:
    return Decimal(str(used)) + Decimal(str(volume)) <= Decimal(str(limit))


def ready_to_shelve(session: Session, node: Node) -> Iterator[tuple[Unit, float]]:
    """Распределить свободные литры между ожидающими в порядке поступления."""
    remaining = Decimal(str(node.mesh_limit_l)) - Decimal(str(used_l(session, node.id)))
    if remaining <= 0:
        return
    rows = session.execute(_rows(session, node.id, ("awaiting_shelf",)).order_by(Unit.id))
    for unit, volume in rows:
        size = Decimal(str(volume))
        if size <= remaining:
            remaining -= size
            yield unit, volume
            if remaining == 0:
                break


def plan_removals(session: Session, node: Node, *, share_changed: bool = False) -> None:
    """Назначить минимальный префикс FIFO под общий объём очереди, исключая резервы."""
    rows = session.execute(
        _rows(session, node.id, (*ON_SHELF, *PENDING))
        .order_by(Unit.shelved_at.nulls_last(), Unit.id)
        .with_for_update(of=Unit)
    ).all()
    used = sum(Decimal(str(v)) for u, v in rows if u.status in ON_SHELF)
    if share_changed and node.pending_share_pct is not None:
        # При выборе новой цели пустую часть полки можно освободить сразу.
        target = Decimal(str(node.planned_limit_l))
        assigned = sum(Decimal(str(v)) for u, v in rows if u.reduction_on is not None)
        set_capacity(node, min(Decimal(str(node.mesh_limit_l)), max(target, used - assigned)))
    limit = Decimal(str(node.mesh_limit_l))
    excess = used - limit
    waiting = committed = Decimal(0)
    for unit, volume in rows:
        litres = Decimal(str(volume))
        if unit.reduction_on is not None:
            if share_changed and excess <= 0:
                unit.reduction_on = None
            else:
                committed += litres
                excess -= litres
        elif unit.status in PENDING:
            if litres > limit:
                unit.status = "awaiting_return"
            else:
                unit.status = "awaiting_shelf"
                waiting += litres
    # Утренние задания уже освободят этот объём. Приём отказов на действующей
    # полке добавляет только недостающие снятия сверх них.
    required = used - committed + waiting - limit if waiting else Decimal(0)
    for unit, volume in rows:
        if unit.status in ("on_shelf", "remove_pending") and unit.reduction_on is None:
            unit.status = "remove_pending" if required > 0 else "on_shelf"
            required -= Decimal(str(volume))
    session.flush()


def set_capacity(node: Node, capacity: Decimal) -> None:
    target = Decimal(str(node.planned_limit_l))
    if capacity <= target:
        node.mesh_share_pct = node.pending_share_pct
        node.active_limit_l = None
        node.pending_share_pct = None
        node.next_reduction_day = None
    else:
        node.active_limit_l = capacity


def morning_batches(session: Session, node: Node) -> Iterator[tuple[Decimal, list[Unit]]]:
    """Прогноз и выполнение используют одни и те же целые FIFO-партии."""
    if node.pending_share_pct is None:
        return
    target = Decimal(str(node.planned_limit_l))
    capacity = Decimal(str(node.mesh_limit_l))
    rows = session.execute(
        _rows(session, node.id, (*ON_SHELF, "awaiting_shelf"))
        .where(Unit.reduction_on.is_(None))
        .order_by(Unit.shelved_at.nulls_last(), Unit.id)
    ).all()
    # На границе дня освобождаем пустую часть. Место для ожидающих размещения
    # сохраняем, в том числе между обычным снятием и постановкой на полку.
    occupied = sum(Decimal(str(volume)) for _, volume in rows)
    morning_capacity = min(capacity, max(target, occupied))
    candidates = [(unit, volume) for unit, volume in rows if unit.status == "on_shelf"]
    pos = 0
    while capacity > target:
        batch = []
        litres = Decimal(0)
        while pos < len(candidates) and litres < morning_capacity - target:
            unit, volume = candidates[pos]
            size = Decimal(str(volume))
            if litres + size > DAILY_REDUCTION_L:
                break
            batch.append(unit)
            litres += size
            pos += 1
        morning_capacity = max(target, morning_capacity - litres)
        if morning_capacity == capacity:
            break
        capacity = morning_capacity
        yield capacity, batch


def estimated_days(session: Session, node: Node) -> int | None:
    if node.pending_share_pct is None:
        return 0
    for days, (capacity, _) in enumerate(morning_batches(session, node), 1):
        if capacity == Decimal(str(node.planned_limit_l)):
            return days
    return None


def advance_mornings(session: Session, today: date) -> None:
    """Назначить наступившие партии в одной транзакции с изменением лимита и даты."""
    nodes = session.scalars(
        select(Node)
        .where(Node.next_reduction_day <= today)
        .order_by(Node.id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    ).all()
    for node in nodes:
        plan_removals(session, node)
        while node.next_reduction_day is not None and node.next_reduction_day <= today:
            step = next(morning_batches(session, node), None)
            if step is None:
                node.next_reduction_day = today + timedelta(days=1)
                break
            capacity, batch = step
            for unit in batch:
                unit.reduction_on = node.next_reduction_day
            node.next_reduction_day += timedelta(days=1)
            set_capacity(node, capacity)
            session.flush()
            plan_removals(session, node)
    session.flush()


def _task(unit: Unit, volume: float, action: str) -> dict:
    return {
        "unit_id": unit.id,
        "version": unit.version,
        "sku": unit.sku,
        "barcode": unit.barcode,
        "volume_l": volume,
        "action": action,
    }


def reduction_plan(session: Session, node: Node, now: datetime) -> tuple[dict | None, list[dict]]:
    """Выдаём оператору только назначенные задания, включая ещё не выполненные со вчера."""
    rows = session.execute(
        _rows(session, node.id, ("on_shelf",))
        .where(Unit.reduction_on.is_not(None))
        .order_by(Unit.reduction_on, Unit.shelved_at.nulls_last(), Unit.id)
    ).all()
    if not rows and node.pending_share_pct is None:
        return None, []
    today = now.astimezone(PVZ_TIMEZONE).date()
    sent = session.scalar(
        select(func.coalesce(func.sum(ReductionDispatch.volume_l), 0)).where(
            ReductionDispatch.pvz_id == node.id, ReductionDispatch.day == today
        )
    )
    tasks = []
    for unit, volume in rows:
        reason = None
        if tasks:
            reason = "Сначала отправьте более старый товар"
        elif sent + Decimal(str(volume)) > DAILY_REDUCTION_L:
            reason = "Остатка дневного лимита недостаточно. Отправка возможна завтра"
        tasks.append(
            {
                **_task(unit, volume, "reduce"),
                "scheduled_for": unit.reduction_on,
                "blocked_reason": reason,
            }
        )
    return {
        "day": today,
        "sent_l": sent,
        "limit_l": DAILY_REDUCTION_L,
        "target_l": node.planned_limit_l,
    }, tasks


def shelf_tasks(session: Session, node: Node) -> list[dict]:
    removals = session.execute(
        _rows(session, node.id, ("remove_pending", "awaiting_return")).order_by(
            Unit.shelved_at, Unit.id
        )
    )
    tasks = [
        _task(unit, volume, "remove" if unit.status == "remove_pending" else "return")
        for unit, volume in removals
    ]
    tasks.extend(_task(unit, volume, "shelve") for unit, volume in ready_to_shelve(session, node))
    return tasks
