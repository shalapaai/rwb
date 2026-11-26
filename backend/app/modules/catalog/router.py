"""Каталог демо-города: продажи товаров, категорий и продавцов.

Все сводки используют то же окно продаж, что и оценка допуска в mesh.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.auth import Role, require_roles
from app.core.db import get_session
from app.modules.catalog import service
from app.modules.catalog.models import Category, Sale
from app.modules.catalog.schemas import CategoryStats, ProductRow, PvzSales, SellerStats
from app.modules.inventory.models import Product, Seller

router = APIRouter(tags=["catalog"])

AnyRole = Annotated[Role, Depends(require_roles(*Role))]
SellerRoles = Annotated[Role, Depends(require_roles(Role.SELLER, Role.LOGIST, Role.ADMIN))]
Db = Annotated[Session, Depends(get_session)]


@router.get("/catalog/products", response_model=list[ProductRow])
def products(_: AnyRole, db: Db, category: str | None = None, seller: str | None = None):
    """Товары города по спросу: продажи за 30 дней, порог модели, можно ли в mesh и почему."""
    return service.product_rows(db, category, seller)


def _sales_window(db: Session) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    today = service._today(db)
    return Sale.day > today - timedelta(days=service.WINDOW_DAYS), Sale.day <= today


@router.get("/catalog/categories")
def categories(_: AnyRole, db: Db) -> list[CategoryStats]:
    """Продажи каждой категории и её доля в продажах города за 30 дней."""
    sales_by_category = db.execute(
        select(Product.category_id, func.sum(Sale.qty))
        .join(Sale, Sale.sku == Product.sku)
        .where(*_sales_window(db))
        .group_by(Product.category_id)
    )
    totals = {category_id: qty for category_id, qty in sales_by_category}
    city_sales = sum(totals.values())
    rows = []
    for category in db.scalars(select(Category)):
        sales = totals.get(category.id, 0)
        rows.append(
            CategoryStats(
                id=category.id,
                name=category.name,
                sales_30d=sales,
                share_pct=100 * sales / city_sales if city_sales else 0,
                mesh_allowed=category.mesh_allowed,
                ban_reason=category.ban_reason,
            )
        )
    return sorted(rows, key=lambda row: (-row.sales_30d, row.id))


@router.get("/sellers/{id}/stats")
def seller_stats(id: str, _: SellerRoles, db: Db) -> SellerStats:
    """Продажи продавца по товарам и ПВЗ, число товаров с допуском в mesh."""
    seller = db.get(Seller, id)
    if seller is None:
        raise HTTPException(404, f"Нет продавца {id}")

    # Каталог вычисляет тот же допуск сразу для всех товаров продавца.
    products = [ProductRow(**row) for row in service.product_rows(db, seller=id)]
    sales = func.sum(Sale.qty)
    by_pvz = db.execute(
        select(Sale.pvz_id, sales)
        .join(Product, Product.sku == Sale.sku)
        .where(Product.seller_id == id, *_sales_window(db))
        .group_by(Sale.pvz_id)
        .order_by(sales.desc(), Sale.pvz_id)
    )
    return SellerStats(
        seller_id=seller.id,
        name=seller.name,
        sales_30d=sum(product.sales_30d for product in products),
        eligible=sum(product.mesh_eligible for product in products),
        total=len(products),
        products=products,
        by_pvz=[PvzSales(pvz_id=pvz_id, sales_30d=qty) for pvz_id, qty in by_pvz],
    )
