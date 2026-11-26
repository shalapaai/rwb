"""Ответы API двойной записи. Суммы — целые копейки."""

from datetime import datetime

from pydantic import BaseModel


class EntryOut(BaseModel):
    account: str
    amount_kop: int


class TxnOut(BaseModel):
    id: int
    scheme: str
    kind: str
    order_id: int | None
    idempotency_key: str | None
    created_at: datetime
    entries: list[EntryOut]
    total_kop: int  # всегда 0 — видно прямо в ответе


class CheckOut(BaseModel):
    """Проверка всего журнала: суммы всех проводок равны нулю."""

    txn_count: int
    unbalanced_txn_ids: list[int]
    ok: bool
