"""Двойная запись: проводка (`ledger_txn`) и её записи (`ledger_entry`).

Сумма записей каждой проводки = 0 — это проверяет `ledger.post()` (задача N3), а не БД.
Деньги — только BigInteger в копейках. Записи не удаляются: возврат — новая проводка.
`scheme`: 'mesh' — что случилось, 'base' — как было бы через хаб (для сравнения).
"""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

SCHEMES = ("mesh", "base")


class LedgerTxn(Base):
    __tablename__ = "ledger_txn"
    __table_args__ = (CheckConstraint(f"scheme IN {SCHEMES}", name="scheme_valid"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scheme: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(16))  # sale, tariff, cost, return, penalty
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LedgerEntry(Base):
    __tablename__ = "ledger_entry"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    txn_id: Mapped[int] = mapped_column(ForeignKey("ledger_txn.id"), index=True)
    account: Mapped[str] = mapped_column(String(64), index=True)  # «pvz:ПВЗ-A», «mp», «ext:van»
    amount_kop: Mapped[int] = mapped_column(BigInteger)  # + начисление, − списание
