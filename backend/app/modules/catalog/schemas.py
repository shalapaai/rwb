"""Ответы каталога и статистики продавца.

Продажи измеряются в штуках за 30 дней, цена товара — в копейках.
Доля категории выражена в процентах от продаж города.
"""

from decimal import Decimal

from pydantic import BaseModel


class ProductRow(BaseModel):
    sku: str
    name: str
    category_id: str | None
    category: str | None
    seller_id: str
    price_kop: int
    volume_l: float
    sales_30d: int
    demand_per_day: Decimal
    threshold_per_day: Decimal
    mesh_eligible: bool
    reason: str


class CategoryStats(BaseModel):
    id: str
    name: str
    sales_30d: int
    share_pct: float
    mesh_allowed: bool
    ban_reason: str | None


class PvzSales(BaseModel):
    pvz_id: str
    sales_30d: int


class SellerStats(BaseModel):
    seller_id: str
    name: str
    sales_30d: int
    eligible: int
    total: int
    products: list[ProductRow]
    by_pvz: list[PvzSales]
