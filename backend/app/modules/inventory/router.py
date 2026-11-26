"""Эндпоинты ПВЗ: приём отказа, настройки mesh-полки, остатки на полке."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Role, require_roles
from app.core.db import get_session
from app.core.idempotency import IdemKey, run_once
from app.modules.inventory import service
from app.modules.inventory.schemas import (
    RefusalIn,
    RefusalOut,
    SettingsIn,
    SettingsOut,
    StockOut,
)

router = APIRouter(prefix="/pvz", tags=["inventory"])

Operator = Annotated[Role, Depends(require_roles(Role.PVZ_OPERATOR, Role.ADMIN))]
AnyRole = Annotated[Role, Depends(require_roles(*Role))]
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

    def apply() -> dict:
        node = service.get_pvz(db, pvz_id, lock=True)
        node.mesh_share_pct = body.mesh_share_pct
        db.flush()
        used = service.used_l(db, pvz_id)
        return {
            "pvz_id": pvz_id,
            "mesh_share_pct": node.mesh_share_pct,
            "used_l": used,
            "limit_l": node.mesh_limit_l,
            "over_limit": used > node.mesh_limit_l,
        }

    result = run_once(db, f"settings:{pvz_id}", key, apply)
    db.commit()
    return result


@router.get("/{pvz_id}/stock", response_model=StockOut)
def get_stock(pvz_id: str, _: AnyRole, db: Db):
    """Единицы на mesh-полке (старые сверху) и «занято / лимит, л»."""
    return service.stock(db, pvz_id)
