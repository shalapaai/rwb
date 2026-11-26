"""Товар: продавцы, карточки товара, физические единицы и отказы/возвраты.

Единица (`units`) — одна штука со штрихкодом. На mesh-полку она попадает только из
отказа при получении: сотрудник ставит оценку A (не вскрыт) или B (упаковку
восстановил); C и возврат после покупки уезжают в хаб (решения 1–2 и N0, AGENTS.md).
`version` — оптимистичная блокировка: SQLAlchemy сам пишет UPDATE … WHERE version = :v
и бросает StaleDataError, если запись успели поменять (инвариант 4).
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

ON_SHELF = ("on_shelf", "reserved", "remove_pending")  # физически занимают место
PENDING = ("awaiting_shelf", "awaiting_return")  # приняты, физически ещё вне полки
UNIT_STATUSES = (*ON_SHELF, *PENDING, "in_transit", "ready", "issued", "to_hub", "at_hub")
GRADES = ("A", "B", "C")
MAX_MESH_UNIT_L = 10
RETURN_KINDS = ("refusal", "return")  # отказ при получении / возврат после покупки
RETURN_DECISIONS = ("shelf", "repack", "hub")


class Seller(Base):
    __tablename__ = "sellers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # «S-01»
    name: Mapped[str] = mapped_column(String(200), default="")


class Product(Base):
    """Карточка товара (SKU)."""

    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("volume_l > 0", name="volume_positive"),
        CheckConstraint("price_kop >= 0", name="price_non_negative"),
    )

    sku: Mapped[str] = mapped_column(String(32), primary_key=True)  # «SKU-001»
    name: Mapped[str] = mapped_column(String(200), default="")
    seller_id: Mapped[str] = mapped_column(ForeignKey("sellers.id"))
    category_id: Mapped[str | None] = mapped_column(ForeignKey("categories.id"))
    volume_l: Mapped[float] = mapped_column(Float)  # объём в упаковке, литры
    price_kop: Mapped[int] = mapped_column(BigInteger)  # цена для покупателя, копейки
    mesh_allowed: Mapped[bool] = mapped_column(Boolean, default=True)  # допуск на mesh-полку


class Unit(Base):
    """Одна физическая штука товара."""

    __tablename__ = "units"
    __table_args__ = (
        CheckConstraint(f"status IN {UNIT_STATUSES}", name="status_valid"),
        CheckConstraint(f"grade IS NULL OR grade IN {GRADES}", name="grade_valid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    barcode: Mapped[str] = mapped_column(String(64), unique=True)
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"), index=True)
    # где лежит сейчас; None — едет в газели
    node_id: Mapped[str | None] = mapped_column(ForeignKey("nodes.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="on_shelf", index=True)
    grade: Mapped[str | None] = mapped_column(String(1))  # оценка сотрудника при отказе
    # когда легла на полку: очередь FIFO и срок 14 дней
    shelved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reduction_on: Mapped[date | None] = mapped_column(Date)  # дата назначения возврата
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __mapper_args__ = {"version_id_col": version}


class ReductionDispatch(Base):
    """Подтверждённая отправка в хаб при снижении доли. Объём фиксируется на дату отправки."""

    __tablename__ = "reduction_dispatches"
    __table_args__ = (Index("ix_reduction_pvz_day", "pvz_id", "day"),)

    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"), primary_key=True)
    pvz_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"))
    day: Mapped[date] = mapped_column(Date)
    volume_l: Mapped[Decimal] = mapped_column(Numeric)


class ShelfWriteOff(Base):
    """Отправка в хаб из-за состояния упаковки или объёма в день стенда."""

    __tablename__ = "shelf_writeoffs"
    __table_args__ = (
        CheckConstraint("category IN ('packaging', 'volume')", name="category_valid"),
        Index("ix_shelf_writeoffs_pvz_day", "pvz_id", "day"),
    )

    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"), primary_key=True)
    pvz_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"))
    day: Mapped[date] = mapped_column(Date)
    category: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)


class Return(Base):
    """Отказ при получении или возврат после покупки и решение по единице."""

    __tablename__ = "returns"
    __table_args__ = (
        CheckConstraint(f"kind IN {RETURN_KINDS}", name="kind_valid"),
        CheckConstraint(f"grade IS NULL OR grade IN {GRADES}", name="grade_valid"),
        CheckConstraint(f"decision IN {RETURN_DECISIONS}", name="decision_valid"),
        # возврат после покупки всегда едет в хаб (решение N0)
        CheckConstraint("kind = 'refusal' OR decision = 'hub'", name="return_goes_to_hub"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    pvz_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"))  # где приняли
    kind: Mapped[str] = mapped_column(String(8))
    grade: Mapped[str | None] = mapped_column(String(1))
    decision: Mapped[str] = mapped_column(String(8))
    reason: Mapped[str] = mapped_column(Text, default="")  # например «полка заполнена»
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
