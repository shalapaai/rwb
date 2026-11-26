"""Заказы покупателей и резервы единиц под них.

Инвариант 3: у единицы не больше одного активного резерва — это держит частичный
уникальный индекс `uq_reservation_active_unit` (unit_id WHERE active), а не код.
Инвариант 4: статус заказа меняется с проверкой `version` (version_id_col SQLAlchemy).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

ORDER_STATUSES = (
    "created",
    "reserved",
    "picked",
    "in_transit",
    "ready",
    "issued",
    "cancelled",
    "returned",
    "rerouted",
)
VARIANTS = ("own", "neighbor", "van", "hub")  # как в routing.models.Variant


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(f"status IN {ORDER_STATUSES}", name="status_valid"),
        CheckConstraint(f"variant IS NULL OR variant IN {VARIANTS}", name="variant_valid"),
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("cost_kop IS NULL OR cost_kop >= 0", name="cost_non_negative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"), index=True)
    qty: Mapped[int] = mapped_column(Integer, default=1)
    # ПВЗ выдачи: где покупатель заберёт заказ
    pvz_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), index=True)
    accepts_neighbor: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="created", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    variant: Mapped[str | None] = mapped_column(String(8))  # выбранный вариант исполнения
    source_node: Mapped[str | None] = mapped_column(String(32))  # откуда товар; «HUB» для хаба
    cost_kop: Mapped[int | None] = mapped_column(BigInteger)  # себестоимость на единицу
    eta_h: Mapped[float | None] = mapped_column(Float)  # часов до готовности к выдаче
    candidates: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)  # варианты с причинами
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __mapper_args__ = {"version_id_col": version}


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        Index(
            "uq_reservation_active_unit",
            "unit_id",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
