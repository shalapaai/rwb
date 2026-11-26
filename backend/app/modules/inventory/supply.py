"""Поставка продавца: принёс товар в ПВЗ или в хаб — система решает, где держать каждую штуку.

Продавец не выбирает mesh сам: система раскладывает партию по потребностям города.
1. Запрет каталога (категория, маркировка, > 10 л) или низкий спрос в городе — всё в хаб.
2. Сдал в ПВЗ: сначала на полку этого ПВЗ — сколько ему нужно (restock.plan: спрос точки
   × 14 сут минус запас, в пределах места); затем в ПВЗ, где не хватает, если туда есть
   попутная газель (what-if); остаток — в хаб.
3. Сдал в хаб: штуки уезжают утренней газелью в ПВЗ, где их не хватает; остаток ждёт в хабе.
Деньги — только ledger.post, суммы — только из economics.params; хранение на полке платит
маркетплейс, как и за остальной запас (AGENTS.md, решение 5).
"""

from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.catalog import service as catalog
from app.modules.economics.params import DEFAULTS as P
from app.modules.economics.params import kopecks
from app.modules.inventory import restock
from app.modules.inventory.models import Product, Unit
from app.modules.inventory.service import sim_now
from app.modules.ledger.service import post
from app.modules.network.models import Node


def _needs(session: Session, sku: str, skip: str | None) -> list[tuple[str, int, str]]:
    """(ПВЗ, сколько штук ему не хватает, почему) — по убыванию спроса точки."""
    out = []
    for node in session.scalars(select(Node).where(Node.kind == "pvz", Node.is_open)):
        if node.id == skip:
            continue
        item = next((i for i in restock.plan(session, node.id)["items"] if i["sku"] == sku), None)
        if item:
            out.append((node.id, item["qty"], item["demand_here_per_day"], item["reason"]))
    out.sort(key=lambda x: (-float(x[2]), x[0]))
    return [(pvz, qty, reason) for pvz, qty, _, reason in out]


def _van_to(session: Session, source: str, dest: str, sku: str, state_box: list) -> str | None:
    """Номер газели, если из source в dest есть попутный путь со свободным местом."""
    from app.modules.orders.service import load_day_state  # orders сам импортирует inventory
    from app.modules.routing.models import Variant
    from app.modules.routing.whatif import WhatIfRequest, what_if

    if not state_box:
        st = load_day_state(session)
        # партия продавца ещё не в базе: для проверки пути считаем её свободным остатком source
        st.stock[(source, sku)] = st.stock.get((source, sku), 0) + 1
        state_box.append(st)
    quote = what_if(state_box[0], WhatIfRequest(source_pvz=source, sku_id=sku, dest_pvz=dest))
    forced = quote.forced
    return forced.legs[0].route_id if forced.variant == Variant.VAN and forced.legs else None


def dropoff(session: Session, node_id: str, sku: str, qty: int, key: str) -> dict:
    node = session.get(Node, node_id)
    if node is None or node.kind not in ("pvz", "hub"):
        raise HTTPException(404, f"Нет ПВЗ или хаба {node_id}")
    if node.kind == "pvz" and not node.is_open:
        raise HTTPException(409, f"{node_id} закрыт — приём невозможен")
    product = session.get(Product, sku)
    if product is None:
        raise HTTPException(404, f"Нет товара {sku}")
    hub = node.id
    if node.kind == "pvz":
        hub = session.scalar(select(Node.id).where(Node.kind == "hub"))
    now = sim_now(session)
    k = f"supply:{node_id}:{key}"
    left = qty
    plan: list[dict] = []  # куда сколько и почему

    ok, why = catalog.eligibility(session, sku) if product.category_id else (True, "")
    if not ok:
        plan.append({"to": hub, "qty": left, "how": "hub", "reason": why})
        left = 0
    if left and node.kind == "pvz":
        here = restock.plan(session, node.id)["items"]
        item = next((i for i in here if i["sku"] == sku), None)
        if item:
            n = min(left, item["qty"])
            plan.append({"to": node.id, "qty": n, "how": "shelf", "reason": item["reason"]})
            left -= n
    state_box: list = []
    for dest, need, reason in _needs(session, sku, node.id) if left else []:
        if not left:
            break
        if node.kind == "pvz":
            route = _van_to(session, node.id, dest, sku, state_box)
            if route is None:
                continue  # попутной газели туда нет — не везём отдельным рейсом
            how, via = "van", f"газелью {route}"
        else:
            how, via = "hub_van", "утренней газелью из хаба"
        n = min(left, need)
        plan.append({"to": dest, "qty": n, "how": how, "reason": f"{reason}; {via}"})
        left -= n
    if left:
        plan.append(
            {"to": hub, "qty": left, "how": "hub", "reason": "в городе этого товара хватает"}
        )

    units, txn_ids = [], []

    def pay(tag: str, legs: list[tuple[str, int]], kind: str = "cost") -> None:
        txn_ids.append(post(session, "mesh", kind, None, legs, key=f"{k}:{tag}").id)

    def receive(pvz: str, n: int, tag: str) -> None:
        fee, work = P.receive_fee_kop * n, kopecks(P.labour(P.receive_min)) * n
        pay(f"{tag}:fee", [("mp", -fee), (f"pvz:{pvz}", fee)], "tariff")
        pay(f"{tag}:labour", [(f"pvz:{pvz}", -work), ("ext:labour", work)])

    if node.kind == "pvz":
        receive(node.id, qty, "intake")  # ПВЗ принимает всю партию у продавца
    for i, step in enumerate(plan):
        n, to, how = step["qty"], step["to"], step["how"]
        on_shelf = how in ("shelf", "van", "hub_van")
        for _ in range(n):
            u = Unit(
                barcode=f"S-{uuid4().hex[:10]}",
                sku=sku,
                node_id=to if on_shelf or node.kind == "hub" else node.id,
                status="on_shelf" if on_shelf else ("at_hub" if node.kind == "hub" else "to_hub"),
                source="seller",
                shelved_at=now if on_shelf else None,
            )
            session.add(u)
            units.append(u)
        if how == "van":
            van = P.van_space_kop * n
            pay(f"{i}:van", [("mp", -van), ("ext:van", van)])
            receive(to, n, f"{i}:dest")
        elif how == "hub_van":
            leg = P.hub_order_kop * n
            pay(f"{i}:hub_van", [("mp", -leg), ("ext:hub_ops", leg)])
            receive(to, n, f"{i}:dest")
        elif how == "hub" and node.kind == "pvz":
            back = P.hub_return_kop * n
            pay(f"{i}:to_hub", [("mp", -back), ("ext:hub_ops", back)])
    session.flush()
    return {
        "node_id": node_id,
        "sku": sku,
        "qty": qty,
        "plan": plan,
        "units": [{"unit_id": u.id, "node_id": u.node_id, "status": u.status} for u in units],
        "txn_ids": txn_ids,
    }
