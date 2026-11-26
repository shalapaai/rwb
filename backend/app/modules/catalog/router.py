"""Каталог демо-города: какие товары покупают и какие отказы можно оставить в mesh (MX1).

Здесь только GET /catalog/products. /catalog/categories и /sellers/{id}/stats — у Никиты (NK2):
он дописывает свои эндпоинты в этот же роутер.
"""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import Role, require_roles
from app.core.db import get_session
from app.modules.catalog import service

router = APIRouter(tags=["catalog"])

AnyRole = Annotated[Role, Depends(require_roles(*Role))]
Db = Annotated[Session, Depends(get_session)]


class ProductRow(BaseModel):
    sku: str
    name: str
    category_id: str | None
    category: str | None
    seller_id: str
    price_kop: int
    volume_l: float
    sales_30d: int  # продаж в городе за последние 30 дней
    demand_per_day: Decimal  # sales_30d / 30
    threshold_per_day: Decimal  # граница спроса из модели: ниже — mesh дороже хаба
    mesh_eligible: bool
    reason: str


@router.get("/catalog/products", response_model=list[ProductRow])
def products(_: AnyRole, db: Db, category: str | None = None, seller: str | None = None):
    """Товары города по спросу: продажи за 30 дней, порог модели, можно ли в mesh и почему."""
    return service.product_rows(db, category, seller)
