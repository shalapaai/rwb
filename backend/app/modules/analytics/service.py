"""Аналитика ПВЗ, продавца и цепочки — из журнала проводок (ledger_entry).

Деньги берём только из проводок, поэтому числа на экранах, в тестах и на слайдах
совпадают. Вид операции (проверка, отбор, приём…) определяем по ключу проводки:
все ключи строятся по одной схеме — «refusal:…:check:…», «order:N:pick:…» и т. д.
Загрузку полки берём из остатков, комиссию отделяем от логистики по ставке продажи.

Главная проверка (`chain`): тарифы — переводы внутри цепочки, поэтому
сумма выгод сторон = экономия цепочки = разница реальных внешних затрат base − mesh.
"""

from collections import defaultdict
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.inventory.models import Product, Unit
from app.modules.inventory.service import ON_SHELF, sim_now
from app.modules.inventory.service import stock as shelf_stock
from app.modules.ledger.models import LedgerEntry, LedgerTxn
from app.modules.network.models import Node
from app.modules.orders import pricing
from app.modules.orders.models import Order

# порядок важен: «order:N:pick:storage» — это хранение, а не отбор
OPS = [
    ("storage", "хранение"),
    ("repack", "переупаковка"),
    ("check", "проверка отказа"),
    ("pick", "отбор"),
    ("receive", "приём из газели"),
    ("issue_fee", "выдача"),
    ("van", "место в газели"),
    ("hub", "хаб и магистраль"),
    ("payout", "выплата продавцу"),
    ("paid", "оплата покупателя"),
]


def op_of(key: str | None, kind: str) -> str:
    if kind == "penalty":
        return "penalty"
    parts = (key or "").split(":")
    return next((op for op, _ in OPS if op in parts), "other")


OP_LABEL = dict(OPS) | {"penalty": "штраф", "other": "прочее"}


@dataclass
class Row:
    order_id: int | None
    scheme: str
    kind: str
    op: str
    account: str
    amount: int
    txn_id: int


def _rows(session: Session, scheme: str | None = None) -> list[Row]:
    q = select(
        LedgerTxn.order_id,
        LedgerTxn.scheme,
        LedgerTxn.kind,
        LedgerTxn.idempotency_key,
        LedgerEntry.account,
        LedgerEntry.amount_kop,
        LedgerTxn.id,
    ).join(LedgerEntry, LedgerEntry.txn_id == LedgerTxn.id)
    if scheme:
        q = q.where(LedgerTxn.scheme == scheme)
    return [
        Row(o, s, k, op_of(key, k), a, v, t) for o, s, k, key, a, v, t in session.execute(q).all()
    ]


# ---------------------------------------------------------------- ПВЗ


def pvz(session: Session, pvz_id: str) -> dict:
    node = session.get(Node, pvz_id)
    if node is None or node.kind != "pvz":
        raise HTTPException(404, f"Нет ПВЗ {pvz_id}")
    account = f"pvz:{pvz_id}"
    rows = _rows(session)
    mine = {r.txn_id for r in rows if r.account == account}

    income: dict[str, int] = defaultdict(int)
    costs: dict[str, int] = defaultdict(int)
    profit = {"mesh": 0, "base": 0}
    penalties = 0
    for r in rows:
        if r.account == account:
            profit[r.scheme] += r.amount
            if r.scheme == "mesh" and r.kind == "tariff" and r.amount > 0:
                income[r.op] += r.amount
            if r.kind == "penalty":
                penalties += -r.amount
        elif r.txn_id in mine and r.scheme == "mesh" and r.kind == "cost":
            costs[r.account.removeprefix("ext:")] += r.amount  # труд, аренда, материалы

    ops_today = session.scalar(
        select(func.count(func.distinct(LedgerTxn.id)))
        .join(LedgerEntry, LedgerEntry.txn_id == LedgerTxn.id)
        .where(
            LedgerEntry.account == account,
            LedgerEntry.amount_kop > 0,
            LedgerTxn.kind == "tariff",
            LedgerTxn.scheme == "mesh",
            func.date(LedgerTxn.created_at) == func.current_date(),
        )
        # тариф самой операции; «order:N:pick:storage» — хранение, операцией не считается
        .where(LedgerTxn.idempotency_key.op("~")(r":(check|repack):tariff$|:(pick|receive):fee$"))
    )
    shelf = shelf_stock(session, pvz_id)
    return {
        "pvz_id": pvz_id,
        "income": [
            {"op": op, "label": OP_LABEL[op], "amount_kop": v} for op, v in sorted(income.items())
        ],
        "income_total_kop": sum(income.values()),
        "costs": [{"resource": res, "amount_kop": v} for res, v in sorted(costs.items())],
        "costs_total_kop": sum(costs.values()),
        "penalties_kop": penalties,
        "profit_kop": profit["mesh"],  # доход − затраты − штрафы
        "profit_base_kop": profit["base"],  # то же ПВЗ в схеме через хаб
        "shelf": {
            "mesh_share_pct": shelf["mesh_share_pct"],
            "used_l": shelf["used_l"],
            "limit_l": shelf["limit_l"],
            "units": len(shelf["units"]),
            "load_pct": round(100 * shelf["used_l"] / shelf["limit_l"]) if shelf["limit_l"] else 0,
        },
        "ops_today": ops_today or 0,
        "ops_limit": node.ops_limit,
    }


# ---------------------------------------------------------------- продавец


def _split(price: int, payout: int) -> dict:
    """Выплата продавцу одной проводкой = цена − комиссия − логистика; делим обратно."""
    commission = pricing.pct(price, pricing.COMMISSION_PCT)
    return {
        "price_kop": price,
        "commission_kop": commission,
        "logistics_kop": price - commission - payout,
        "storage_kop": 0,  # хранение отказа оплачивает маркетплейс (решение N0)
        "payout_kop": payout,
    }


def seller(session: Session, seller_id: str) -> dict:
    account = f"seller:{seller_id}"
    rows = _rows(session)
    paid_txn = {r.txn_id for r in rows if r.account == account}
    orders_seen = {r.order_id for r in rows if r.txn_id in paid_txn}
    per: dict[tuple[int, str], dict[str, int]] = defaultdict(lambda: {"price": 0, "payout": 0})
    for r in rows:
        if r.order_id not in orders_seen:
            continue
        if r.account == "ext:buyer":
            per[(r.order_id, r.scheme)]["price"] -= r.amount
        elif r.account == account:
            per[(r.order_id, r.scheme)]["payout"] += r.amount

    orders = {o.id: o for o in session.scalars(select(Order).where(Order.id.in_(orders_seen)))}
    out_rows = []
    for oid in sorted(orders):
        o = orders[oid]
        mesh = per.get((oid, "mesh"))
        base = per.get((oid, "base"))
        m = _split(mesh["price"], mesh["payout"]) if mesh and mesh["price"] else None
        b = _split(base["price"], base["payout"]) if base and base["price"] else None
        out_rows.append(
            {
                "order_id": oid,
                "sku": o.sku,
                "status": o.status,
                "variant": o.variant,
                "mesh": m,  # None, пока заказ не выдан: продажа проводится при выдаче
                "base": b,
                "gain_kop": (m["payout_kop"] - b["payout_kop"]) if m and b else None,
            }
        )

    now = sim_now(session)
    shelf_units = session.execute(
        select(Unit, Product)
        .join(Product, Product.sku == Unit.sku)
        .where(Product.seller_id == seller_id, Unit.status.in_(ON_SHELF))
        .order_by(Unit.shelved_at)
    ).all()
    issued = [r for r in out_rows if r["mesh"]]
    return {
        "seller_id": seller_id,
        "orders": out_rows,
        "refusals_on_shelves": [
            {
                "unit_id": u.id,
                "sku": u.sku,
                "pvz_id": u.node_id,
                "grade": u.grade,
                "status": u.status,
                "age_days": (now - u.shelved_at).days if u.shelved_at else 0,
            }
            for u, _ in shelf_units
        ],
        "summary": {
            "orders": len(out_rows),
            "issued": len(issued),
            "resold_locally": sum(1 for r in issued if r["variant"] != "hub"),
            "payout_mesh_kop": sum(r["mesh"]["payout_kop"] for r in issued),
            "payout_base_kop": sum(r["base"]["payout_kop"] for r in issued if r["base"]),
            "gain_kop": sum(r["gain_kop"] or 0 for r in issued),
            "refusals_on_shelves": len(shelf_units),
        },
    }


# ---------------------------------------------------------------- цепочка


def _is_party(account: str) -> bool:
    return account == "mp" or account.startswith(("seller:", "pvz:"))


def _party(account: str) -> str:
    return account.split(":")[0]  # mp / seller / pvz


def chain(session: Session, order_id: int | None = None) -> dict:
    """Сравнение схем на одних и тех же заказах: только выданные (продажа в обеих схемах)."""
    q = select(Order.id).where(Order.status == "issued")
    if order_id is not None:
        q = q.where(Order.id == order_id)
    issued = set(session.scalars(q))
    rows = _rows(session)

    acc = {s: defaultdict(int) for s in ("mesh", "base")}
    per_order: dict[int, dict[str, int]] = defaultdict(lambda: {"mesh": 0, "base": 0})
    overhead_party = overhead_ext = 0  # обработка отказов: проверка и переупаковка без заказа
    for r in rows:
        if r.order_id in issued:
            key = "party:" + _party(r.account) if _is_party(r.account) else r.account
            acc[r.scheme][key] += r.amount
            if r.account.startswith("ext:") and r.account != "ext:buyer":
                per_order[r.order_id][r.scheme] += r.amount
        elif r.order_id is None and r.scheme == "mesh" and order_id is None:
            if _is_party(r.account):
                overhead_party += r.amount
            elif r.account != "ext:buyer":
                overhead_ext += r.amount

    def ext_costs(s: str) -> int:
        return sum(v for k, v in acc[s].items() if k.startswith("ext:") and k != "ext:buyer")

    parties = {
        p: acc["mesh"][f"party:{p}"] - acc["base"][f"party:{p}"] for p in ("seller", "pvz", "mp")
    }
    effect = sum(acc["mesh"][k] for k in acc["mesh"] if k.startswith("party:")) - sum(
        acc["base"][k] for k in acc["base"] if k.startswith("party:")
    )
    saved_ext = ext_costs("base") - ext_costs("mesh")
    same_buyer = acc["mesh"]["ext:buyer"] == acc["base"]["ext:buyer"]
    return {
        "orders": len(issued),
        "buyer_paid_mesh_kop": -acc["mesh"]["ext:buyer"],
        "buyer_paid_base_kop": -acc["base"]["ext:buyer"],
        "ext_costs_mesh_kop": ext_costs("mesh"),
        "ext_costs_base_kop": ext_costs("base"),
        "costs_by_account": {
            s: {k: v for k, v in sorted(acc[s].items()) if k.startswith("ext:")} for s in acc
        },
        "effect_kop": effect,  # экономия цепочки: участники в mesh − участники в base
        "parties_kop": parties,  # кому сколько досталось
        "check": {
            "parties_sum_equals_effect": sum(parties.values()) == effect,
            "effect_equals_saved_costs": effect == saved_ext and same_buyer,
            "buyer_pays_the_same": same_buyer,
            "ok": sum(parties.values()) == effect == saved_ext and same_buyer,
        },
        "refusal_handling_kop": {  # затраты на отказы, ещё не проданные или не привязанные
            "parties": overhead_party,
            "ext_costs": overhead_ext,
        },
        "effect_after_refusal_handling_kop": effect + overhead_party,
        "per_order": [
            {
                "order_id": oid,
                "ext_costs_mesh_kop": v["mesh"],
                "ext_costs_base_kop": v["base"],
                "saved_kop": v["base"] - v["mesh"],
            }
            for oid, v in sorted(per_order.items())
        ],
    }
