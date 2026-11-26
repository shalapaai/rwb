"""Эндпоинты ПВЗ: приём отказа, настройки mesh-полки, остатки на полке."""

from hashlib import sha256
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import Role, require_roles
from app.core.db import get_session
from app.core.idempotency import IdemKey, run_once
from app.modules.inventory import restock, service, supply
from app.modules.inventory.schemas import (
    RefusalIn,
    RefusalOut,
    SettingsIn,
    SettingsOut,
    ShelfActionOut,
    StockOut,
)
from app.modules.orders.schemas import VersionIn

router = APIRouter(prefix="/pvz", tags=["inventory"])

Operator = Annotated[Role, Depends(require_roles(Role.PVZ_OPERATOR, Role.ADMIN))]
AnyRole = Annotated[Role, Depends(require_roles(*Role))]
Supply = Annotated[Role, Depends(require_roles(Role.PVZ_OPERATOR, Role.LOGIST, Role.ADMIN))]
Dropoff = Annotated[
    Role, Depends(require_roles(Role.PVZ_OPERATOR, Role.SELLER, Role.LOGIST, Role.ADMIN))
]


class SupplyIn(BaseModel):
    sku_id: str
    qty: int = Field(ge=1, le=500)
Db = Annotated[Session, Depends(get_session)]


@router.post("/{pvz_id}/refusals", response_model=RefusalOut)
def accept_refusal(pvz_id: str, body: RefusalIn, key: IdemKey, _: Operator, db: Db):
    """Отказ при получении: оценка A/B/C → mesh-полка или хаб, начисления ПВЗ."""
    result = run_once(
        db,
        f"refusal:{pvz_id}",
        key,
        lambda: service.accept_refusal(
            db, pvz_id, body.sku_id, body.grade, key, body.order_id, body.barcode, body.comment
        ),
    )
    db.commit()
    return result


@router.patch("/{pvz_id}/settings", response_model=SettingsOut)
def update_settings(pvz_id: str, body: SettingsIn, key: IdemKey, _: Operator, db: Db):
    """ПВЗ сам выбирает долю склада под mesh: 10–50%."""

    result = run_once(
        db,
        f"settings:{pvz_id}",
        key,
        lambda: service.change_share(db, pvz_id, body.mesh_share_pct),
    )
    db.commit()
    return result


@router.get("/{pvz_id}/stock", response_model=StockOut)
def get_stock(pvz_id: str, _: AnyRole, db: Db):
    """Единицы на mesh-полке (старые сверху) и «занято / лимит, л»."""
    return service.stock(db, pvz_id)


@router.post("/{pvz_id}/units/{unit_id}/{action}", response_model=ShelfActionOut)
def shelf_action(
    pvz_id: str,
    unit_id: int,
    action: Literal["remove", "return", "shelve", "reduce"],
    body: VersionIn,
    key: IdemKey,
    _: Operator,
    db: Db,
):
    """Подтвердить снятие для возврата или размещение товара с доступным заданием."""
    result = run_once(
        db,
        f"shelf:{unit_id}:{action}:{pvz_id}",
        sha256(key.encode()).hexdigest(),  # область и ключ вместе должны помещаться в 128 символов
        lambda: service.complete_shelf_task(db, pvz_id, unit_id, action, body.version),
    )
    db.commit()
    return result


@router.get("/{pvz_id}/restock-plan")
def restock_plan(pvz_id: str, _: AnyRole, db: Db) -> dict:
    """Что привезти из хаба на полку: популярные в этом ПВЗ товары, штуки и почему (HB1)."""
    return restock.plan(db, pvz_id)


@router.post("/{pvz_id}/restock")
def restock_from_hub(pvz_id: str, key: IdemKey, _: Supply, db: Db) -> dict:
    """Привезти из хаба по плану: единицы ложатся на полку, проводки mesh (HB1)."""
    result = run_once(db, f"restock:{pvz_id}", key, lambda: restock.restock(db, pvz_id, key))
    db.commit()
    return result


@router.post("/{node_id}/supply")
def seller_supply(node_id: str, body: SupplyIn, key: IdemKey, _: Dropoff, db: Db) -> dict:
    """Продавец сдал партию в ПВЗ или в хаб (node_id = HUB): система решает по каждой штуке —
    полка этого ПВЗ, газелью в ПВЗ, где не хватает, или хаб (HB1)."""
    result = run_once(
        db,
        f"supply:{node_id}",
        key,
        lambda: supply.dropoff(db, node_id, body.sku_id, body.qty, key),
    )
    db.commit()
    return result
