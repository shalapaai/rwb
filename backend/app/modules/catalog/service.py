"""Спрос на товары в демо-городе и допуск отказа на mesh-полку (MX1).

Спрос товара = его продажи во всех ПВЗ города за последние 30 дней / 30, шт. в сутки.
Порог = граница спроса из модели (economics.sensitivity.boundaries, параметр
demand_per_day): ниже неё mesh обходится дороже хаба. Решение — catalog.rules.decide.
Данные — таблицы categories и sales из NK1 (catalog.models) и products.category_id.
"""

from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import Session

from app.modules.catalog.models import Category, Sale
from app.modules.catalog.rules import ProductFacts, decide
from app.modules.economics.schemas import StressParameter
from app.modules.economics.sensitivity import boundaries
from app.modules.inventory.models import Product
from app.modules.inventory.service import sim_now

WINDOW_DAYS = 30


@lru_cache(maxsize=1)
def threshold_per_day() -> Decimal:
    """Порог спроса из модели; если mesh выгоден при любом спросе — 0."""
    rule = next(b for b in boundaries() if b.param == StressParameter.DEMAND).chain
    return Decimal(str(rule.threshold)) if rule.threshold is not None else Decimal(0)


def _today(session: Session) -> date:
    """День стенда; если тестовых часов нет — последний день в истории продаж."""
    try:
        return sim_now(session).date()
    except NoResultFound:
        return session.scalar(select(func.max(Sale.day))) or date.today()


def demand(session: Session) -> dict[str, tuple[int, Decimal]]:
    """sku → (продаж за 30 дней, спрос в сутки) по всему городу."""
    today = _today(session)
    since = today - timedelta(days=WINDOW_DAYS)
    rows = session.execute(
        select(Sale.sku, func.sum(Sale.qty))
        .where(Sale.day > since, Sale.day <= today)
        .group_by(Sale.sku)
    ).all()
    return {sku: (int(q), Decimal(int(q)) / WINDOW_DAYS) for sku, q in rows}


def product_rows(
    session: Session, category: str | None = None, seller: str | None = None
) -> list[dict]:
    """Строки /catalog/products: товар, продажи за 30 дней, порог модели, допуск и причина."""
    q = select(Product, Category).outerjoin(Category, Category.id == Product.category_id)
    if category:
        q = q.where(Product.category_id == category)
    if seller:
        q = q.where(Product.seller_id == seller)
    stats = demand(session)
    threshold = threshold_per_day()
    rows = []
    for product, cat in session.execute(q).all():
        sold, per_day = stats.get(product.sku, (0, Decimal(0)))
        ok, reason = _decide(product, cat, per_day, threshold)
        rows.append(
            {
                "sku": product.sku,
                "name": product.name,
                "category_id": product.category_id,
                "category": cat.name if cat else None,
                "seller_id": product.seller_id,
                "price_kop": product.price_kop,
                "volume_l": product.volume_l,
                "sales_30d": sold,
                "demand_per_day": per_day,
                "threshold_per_day": threshold,
                "mesh_eligible": ok,
                "reason": reason,
            }
        )
    rows.sort(key=lambda x: (-x["demand_per_day"], x["sku"]))
    return rows


def _decide(
    product: Product, cat: Category | None, per_day: Decimal, threshold: Decimal
) -> tuple[bool, str]:
    if cat is None:
        return False, "у товара нет категории"
    facts = ProductFacts(
        volume_l=product.volume_l,
        product_allowed=product.mesh_allowed,
        category_allowed=cat.mesh_allowed,
        category_ban_reason=cat.ban_reason,
        marked=cat.marked,
    )
    return decide(facts, per_day, threshold)


def eligibility(session: Session, sku: str) -> tuple[bool, str]:
    """Можно ли оставить отказ этого товара на mesh-полке и почему (для приёма отказа, MX2)."""
    product = session.get(Product, sku)
    if product is None:
        raise HTTPException(404, f"Нет товара {sku}")
    cat = session.get(Category, product.category_id) if product.category_id else None
    sold, per_day = demand(session).get(sku, (0, Decimal(0)))
    return _decide(product, cat, per_day, threshold_per_day())
