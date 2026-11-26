"""Шесть сценариев защиты одной кнопкой (docs/04): шаги и снимок «до / после».

Каждый шаг вызывает тот же сервис, что и API (приём отказа, заказ, действия ПВЗ,
закрытие ПВЗ, тестовые часы), — никаких отдельных «демо-путей». До и после
сценария снимаем остатки на полках, статусы заказов и единиц, новые проводки
(с проверкой «сумма каждой = 0») и уведомления, — это и есть отчёт для жюри.
"""

from collections import Counter
from collections.abc import Callable
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.db import Base
from app.modules import all_models  # noqa: F401 — все таблицы, чтобы очистить каждую
from app.modules.catalog import service as catalog
from app.modules.catalog.models import Category
from app.modules.economics.params import DEFAULTS as P
from app.modules.inventory import service as inventory
from app.modules.inventory.models import Product, Unit
from app.modules.ledger.models import LedgerEntry, LedgerTxn
from app.modules.network.models import Node
from app.modules.ops import service as ops
from app.modules.ops.models import Notification
from app.modules.orders import service as orders
from app.modules.orders.models import Order
from app.seed import seed

A, B, C = "ПВЗ-A", "ПВЗ-B", "ПВЗ-C"

TITLES = {
    1: "Отказ в ПВЗ-A → заказ в ПВЗ-B → газель G1 → выдача",
    2: "Заказ без городского остатка → хаб и магистраль (ИМИТАЦИЯ)",
    3: "Возврат после выдачи → оценка → хаб",
    4: "ПВЗ-B закрылся, пока товар в газели G1",
    5: "Отказ без спроса: уведомление на 7-й день, решение на 14-й",
    6: "Непопулярный или маркированный отказ → хаб, хит → полка",
}


def reset(session: Session) -> dict:
    """Чистая БД и демо-город. Повтор даёт то же состояние — сброс идемпотентен сам по себе."""
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    seed(session)
    session.flush()
    return snapshot(session)


# ---------------------------------------------------------------- снимок


def snapshot(session: Session) -> dict:
    shelf = session.execute(
        select(Unit.node_id, Unit.sku, func.count())
        .where(Unit.status == "on_shelf")
        .group_by(Unit.node_id, Unit.sku)
    ).all()
    units = session.execute(select(Unit.status, func.count()).group_by(Unit.status)).all()
    return {
        "shelf": {f"{n} · {s}": q for n, s, q in sorted(shelf)},
        "units": dict(sorted(units)),
        "orders": {o.id: o.status for o in session.scalars(select(Order).order_by(Order.id))},
        "closed_pvz": sorted(session.scalars(select(Node.id).where(Node.is_open.is_(False)))),
        "last_txn": session.scalar(select(func.max(LedgerTxn.id))) or 0,
        "last_notification": session.scalar(select(func.max(Notification.id))) or 0,
    }


def _ledger_since(session: Session, txn_id: int) -> dict:
    rows = session.execute(
        select(
            LedgerTxn.id,
            LedgerTxn.scheme,
            LedgerTxn.kind,
            LedgerEntry.account,
            LedgerEntry.amount_kop,
        )
        .join(LedgerEntry, LedgerEntry.txn_id == LedgerTxn.id)
        .where(LedgerTxn.id > txn_id)
        .order_by(LedgerTxn.id)
    ).all()
    per_txn: Counter[int] = Counter()
    by_account: dict[str, Counter[str]] = {"mesh": Counter(), "base": Counter()}
    kinds: Counter[str] = Counter()
    seen = set()
    for t, scheme, kind, account, amount in rows:
        per_txn[t] += amount
        by_account[scheme][account] += amount
        if t not in seen:
            seen.add(t)
            kinds[f"{scheme}:{kind}"] += 1
    return {
        "txn_count": len(seen),
        "unbalanced": [t for t, s in per_txn.items() if s != 0],
        "by_kind": dict(sorted(kinds.items())),
        "by_account": {
            s: dict(sorted((a, v) for a, v in c.items() if v)) for s, c in by_account.items()
        },
    }


def diff(session: Session, before: dict, after: dict) -> dict:
    keys = sorted(set(before["shelf"]) | set(after["shelf"]))
    shelf = [
        {"place": k, "before": before["shelf"].get(k, 0), "after": after["shelf"].get(k, 0)}
        for k in keys
        if before["shelf"].get(k, 0) != after["shelf"].get(k, 0)
    ]
    orders_changed = [
        {"order_id": oid, "before": before["orders"].get(oid), "after": status}
        for oid, status in after["orders"].items()
        if before["orders"].get(oid) != status
    ]
    notes = session.scalars(
        select(Notification)
        .where(Notification.id > before["last_notification"])
        .order_by(Notification.id)
    )
    ledger = _ledger_since(session, before["last_txn"])
    return {
        "shelf": shelf,
        "units": {"before": before["units"], "after": after["units"]},
        "orders": orders_changed,
        "closed_pvz": {"before": before["closed_pvz"], "after": after["closed_pvz"]},
        "ledger": ledger,
        "notifications": [{"role": n.role, "text": n.text} for n in notes],
        "money_ok": not ledger["unbalanced"],
    }


# ---------------------------------------------------------------- шаги


class Run:
    """Сценарий как список шагов: каждый — название и результат вызова сервиса."""

    def __init__(self, session: Session):
        self.s = session
        self.steps: list[dict] = []
        self.checks: list[dict] = []

    def step(self, name: str, fn: Callable[[], object]) -> object:
        result = fn()
        self.steps.append({"step": name, "result": _brief(result)})
        return result

    def check(self, what: str, ok: bool) -> None:
        self.checks.append({"check": what, "ok": bool(ok)})

    def order_steps(self, order: Order, actions: list[str]) -> Order:
        for a in actions:
            order = self.step(
                f"ПВЗ: {a}", lambda a=a, o=order: orders.act(self.s, o.id, a, o.version)
            )
        return order


def _brief(result: object) -> object:
    if isinstance(result, Order):
        chosen = next((c for c in result.candidates or [] if c.get("chosen")), {})
        return {
            "order_id": result.id,
            "status": result.status,
            "variant": result.variant,
            "source": result.source_node,
            "pvz": result.pvz_id,
            "cost_kop": result.cost_kop,
            "reason": chosen.get("reason"),
        }
    if isinstance(result, dict):
        return {k: v for k, v in result.items() if k not in ("candidates", "notified")}
    return result


def _key() -> str:
    return uuid4().hex  # каждый прогон — новые операции, а не повтор старых


def _scenario_1(r: Run) -> Order:
    refusal = r.step(
        "ПВЗ-A: отказ при получении, оценка A",
        lambda: inventory.accept_refusal(r.s, A, "SKU-001", "A", _key()),
    )
    r.check("отказ лёг на полку", refusal["placement"] == "shelf")
    order = r.step(
        "Покупатель заказал SKU-001 в ПВЗ-B", lambda: orders.create_order(r.s, "SKU-001", B, False)
    )
    r.check("выбрана попутная газель из ПВЗ-A", (order.variant, order.source_node) == ("van", A))
    order = r.order_steps(order, ["pick", "load", "receive", "issue"])
    r.check("заказ выдан", order.status == "issued")
    return order


def _scenario_2(r: Run) -> None:
    order = r.step(
        "Покупатель заказал SKU-002 в ПВЗ-B", lambda: orders.create_order(r.s, "SKU-002", B, False)
    )
    r.check("городского запаса нет — вариант «хаб»", order.variant == "hub")
    r.check("цена хаба из модели", order.cost_kop == P.hub_order_kop)
    r.step(
        "Хаб и магистраль: ИМИТАЦИЯ (/stub/hub)",
        lambda: {"imitation": True, "price_kop": P.hub_order_kop, "hours": P.hub_delivery_min / 60},
    )
    order = r.order_steps(order, ["receive", "issue"])
    r.check("заказ выдан", order.status == "issued")


def _scenario_3(r: Run) -> None:
    order = _scenario_1(r)  # подготовка: нужен выданный заказ
    order = r.step(
        "ПВЗ-B: возврат после выдачи, оценка A",
        lambda: orders.return_order(r.s, order.id, order.version, "A"),
    )
    r.check("заказ возвращён", order.status == "returned")
    unit = r.s.scalar(select(Unit).where(Unit.status == "to_hub", Unit.node_id == B))
    r.check("единица едет в хаб даже при оценке A (решение N0)", unit is not None)


def _scenario_4(r: Run) -> None:
    order = r.step(
        "Покупатель заказал SKU-001 в ПВЗ-B", lambda: orders.create_order(r.s, "SKU-001", B, False)
    )
    order = r.order_steps(order, ["pick", "load"])
    res = r.step(
        "ПВЗ-B закрылся; покупатель согласен забрать в другом ПВЗ",
        lambda: ops.close_pvz(r.s, B, True, None),
    )
    rerouted = res["rerouted"][0] if res["rerouted"] else {}
    r.check("заказ перенаправлен в ПВЗ-C той же газелью", rerouted.get("pvz_id") == C)
    r.check("штраф закрывшемуся ПВЗ", res["penalty_kop"] > 0)
    r.s.refresh(order)
    order = r.order_steps(order, ["receive", "issue"])
    r.check("выдан в ПВЗ-C", order.status == "issued" and order.pvz_id == C)

    def reopen() -> dict:
        r.s.get(Node, B).is_open = True  # инцидент снят: ПВЗ-B снова в плане
        r.s.flush()
        return {"pvz": B, "is_open": True}

    r.step("Инцидент снят: ПВЗ-B снова работает", reopen)


def _scenario_5(r: Run) -> None:
    refusal = r.step(
        "ПВЗ-A: отказ SKU-001, оценка A; заказов на него нет",
        lambda: inventory.accept_refusal(r.s, A, "SKU-001", "A", _key()),
    )
    uid = refusal["unit_id"]
    week = r.step("Прошло 7 суток", lambda: ops.shift_clock(r.s, 7))
    r.check("на 7-й день — уведомление", uid in [n["unit_id"] for n in week["notified"]])
    two = r.step("Прошло ещё 7 суток", lambda: ops.shift_clock(r.s, 7))
    mine = next((d for d in two["decided"] if d["unit_id"] == uid), {})
    r.check("на 14-й день — решение по отказу", bool(mine))
    r.check("хранение за 14 суток проведено", mine.get("storage_kop", 0) > 0)
    r.steps[-1]["result"] = {
        "offset_days": two["offset_days"],
        "decided": len(two["decided"]),
        "this_refusal": mine,
    }


def _scenario_6(r: Run) -> None:
    rows = catalog.product_rows(r.s)
    # Товар без спроса: категория разрешена, но продаж в городе меньше порога модели.
    # Берём из каталога, а не по номеру: спрос зависит от дня стенда (сценарий 5 сдвигает часы).
    dud = min(
        (x for x in rows if not x["mesh_eligible"] and x["reason"].startswith("спрос")),
        key=lambda x: (x["demand_per_day"], x["sku"]),
    )
    shoe = r.s.scalar(
        select(Product.sku)
        .join(Category, Category.id == Product.category_id)
        .where(Category.marked)
        .order_by(Product.sku)
    )
    hit = r.step(
        "ПВЗ-A: отказ хита SKU-001, оценка A",
        lambda: inventory.accept_refusal(r.s, A, "SKU-001", "A", _key()),
    )
    r.check("хит лёг на полку", hit["placement"] == "shelf")
    low = r.step(
        f"ПВЗ-A: отказ {dud['sku']} ({dud['name']}), {dud['sales_30d']} продаж за 30 дней",
        lambda: inventory.accept_refusal(r.s, A, dud["sku"], "A", _key()),
    )
    r.check(
        "товар без спроса едет в хаб: спрос ниже порога модели",
        low["placement"] == "hub" and "ниже порога" in low["reason"],
    )
    marked = r.step(
        f"ПВЗ-A: отказ {shoe}, маркированная категория, оценка A",
        lambda: inventory.accept_refusal(r.s, A, shoe, "A", _key()),
    )
    r.check(
        "маркированный товар едет в хаб, даже если не вскрыт",
        marked["placement"] == "hub" and "аркир" in marked["reason"],
    )


SCENARIOS = {
    1: _scenario_1,
    2: _scenario_2,
    3: _scenario_3,
    4: _scenario_4,
    5: _scenario_5,
    6: _scenario_6,
}


def run(session: Session, n: int) -> dict:
    if n not in SCENARIOS:
        raise HTTPException(404, f"Нет сценария {n}: доступны 1–6")
    before = snapshot(session)
    r = Run(session)
    SCENARIOS[n](r)
    session.flush()
    after = snapshot(session)
    changes = diff(session, before, after)
    r.check("каждая новая проводка в сумме 0", changes["money_ok"])
    return {
        "scenario": n,
        "title": TITLES[n],
        "steps": r.steps,
        "checks": r.checks,
        "ok": all(c["ok"] for c in r.checks),
        **changes,
    }
