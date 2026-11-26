"""Экраны аналитики: ПВЗ, продавец, цепочка (mesh против базовой схемы через хаб)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Role, require_roles
from app.core.db import get_session
from app.modules.analytics import service

router = APIRouter(prefix="/analytics", tags=["analytics"])

PvzRoles = Annotated[Role, Depends(require_roles(Role.PVZ_OPERATOR, Role.LOGIST, Role.ADMIN))]
SellerRoles = Annotated[Role, Depends(require_roles(Role.SELLER, Role.LOGIST, Role.ADMIN))]
AnyRole = Annotated[Role, Depends(require_roles(*Role))]
Db = Annotated[Session, Depends(get_session)]


@router.get("/pvz/{pvz_id}")
def pvz(pvz_id: str, _: PvzRoles, db: Db) -> dict[str, Any]:
    """Доход по операциям, затраты на труд, место и материалы, прибыль, полка, лимит."""
    return service.pvz(db, pvz_id)


@router.get("/seller/{seller_id}")
def seller(seller_id: str, _: SellerRoles, db: Db) -> dict[str, Any]:
    """Деньги по каждому заказу в mesh и через хаб, отказы на полках, сводка."""
    return service.seller(db, seller_id)


@router.get("/chain")
def chain(_: AnyRole, db: Db, order_id: int | None = None) -> dict[str, Any]:
    """Эффект цепочки и проверка: сумма выгод сторон = экономия = разница внешних затрат."""
    return service.chain(db, order_id)
