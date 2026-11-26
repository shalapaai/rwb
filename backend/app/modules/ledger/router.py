"""Эндпоинты двойной записи: чтение проводок и проверка журнала.

Записывать проводки через API нельзя — только сервисы модулей вызывают ledger.post().
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import Role, require_roles
from app.core.db import get_session
from app.modules.ledger.models import LedgerTxn
from app.modules.ledger.schemas import CheckOut, EntryOut, TxnOut
from app.modules.ledger.service import entries_of, unbalanced_txn_ids

router = APIRouter(prefix="/ledger", tags=["ledger"])

AnyRole = Annotated[Role, Depends(require_roles(*Role))]
Db = Annotated[Session, Depends(get_session)]


@router.get("", response_model=list[TxnOut])
def list_txns(_: AnyRole, db: Db, order_id: int | None = None, scheme: str | None = None):
    """Проводки с записями; фильтры — по заказу и схеме (mesh / base)."""
    q = select(LedgerTxn).order_by(LedgerTxn.id)
    if order_id is not None:
        q = q.where(LedgerTxn.order_id == order_id)
    if scheme is not None:
        q = q.where(LedgerTxn.scheme == scheme)
    out = []
    for t in db.scalars(q):
        entries = [
            EntryOut(account=e.account, amount_kop=e.amount_kop) for e in entries_of(db, t.id)
        ]
        out.append(
            TxnOut(
                id=t.id,
                scheme=t.scheme,
                kind=t.kind,
                order_id=t.order_id,
                idempotency_key=t.idempotency_key,
                created_at=t.created_at,
                entries=entries,
                total_kop=sum(e.amount_kop for e in entries),
            )
        )
    return out


@router.get("/check", response_model=CheckOut)
def check(_: AnyRole, db: Db):
    """«Деньги сходятся»: у каждой проводки сумма записей = 0. Для экрана и жюри."""
    bad = unbalanced_txn_ids(db)
    count = db.scalar(select(func.count()).select_from(LedgerTxn)) or 0
    return CheckOut(txn_count=count, unbalanced_txn_ids=bad, ok=not bad)
