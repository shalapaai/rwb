"""Операции: инциденты, уведомления, ключи идемпотентности и тестовое время.

`idempotency_keys` хранит ответ на изменяющий запрос: повтор с тем же ключом
отдаёт его же и ничего не меняет (инвариант 5, хелпер — app/core/idempotency.py, N4).
`sim_clock` — одна строка: на сколько дней сдвинуто время (сценарий долгого хранения).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Incident(Base):
    """Сбой узла: например, ПВЗ закрылся с товаром в пути. Штраф — в копейках."""

    __tablename__ = "incidents"
    __table_args__ = (CheckConstraint("penalty_kop >= 0", name="penalty_non_negative"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # «pvz_closed»
    penalty_kop: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    """Уведомление в UI (без SMS и ботов — их заменяет экран, правило организаторов)."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String(16), index=True)  # кому: seller, pvz_operator, …
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SimClock(Base):
    __tablename__ = "sim_clock"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    offset_days: Mapped[int] = mapped_column(Integer, default=0)
