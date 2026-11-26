"""Сеть: узлы (ПВЗ и хаб) и маршруты газелей на день.

Маршруты газелей приходят от WB как есть — это вход алгоритма routing.
Здесь же лимит mesh-полки ПВЗ: доля склада 10–50% выбирает сам ПВЗ (решение 4, AGENTS.md).
"""

from datetime import date

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

NODE_KINDS = ("pvz", "hub")


class Node(Base):
    __tablename__ = "nodes"
    __table_args__ = (
        CheckConstraint(f"kind IN {NODE_KINDS}", name="kind_valid"),
        CheckConstraint("mesh_share_pct BETWEEN 10 AND 50", name="mesh_share_range"),
        CheckConstraint("storage_total_l >= 0", name="storage_non_negative"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # «ПВЗ-A», «HUB»
    kind: Mapped[str] = mapped_column(String(8), default="pvz")
    name: Mapped[str] = mapped_column(String(200), default="")
    x_km: Mapped[float] = mapped_column(Float)  # координаты на синтетической карте, км
    y_km: Mapped[float] = mapped_column(Float)
    storage_total_l: Mapped[float] = mapped_column(Float, default=0.0)  # весь склад ПВЗ, литры
    mesh_share_pct: Mapped[int] = mapped_column(Integer, default=25)  # доля склада под mesh
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    ops_limit: Mapped[int] = mapped_column(Integer, default=60)  # mesh-операций в день

    @property
    def mesh_limit_l(self) -> float:
        """Сколько литров ПВЗ отдал под mesh-полку."""
        return self.storage_total_l * self.mesh_share_pct / 100


class VanRoute(Base):
    """Рейс газели от хаба на конкретную дату (версия растёт при правке плана)."""

    __tablename__ = "van_routes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # «G1»
    route_date: Mapped[date] = mapped_column(Date)
    depot_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"))
    capacity: Mapped[int] = mapped_column(Integer, default=300)  # мест в кузове
    version: Mapped[int] = mapped_column(Integer, default=1)


class VanStop(Base):
    """Остановка рейса. Время — минуты от выезда газели."""

    __tablename__ = "van_stops"
    __table_args__ = (
        UniqueConstraint("route_id", "seq", name="uq_stop_seq"),  # одна позиция — одна остановка
        CheckConstraint("eta_min >= 0", name="eta_non_negative"),
        CheckConstraint("service_min >= 0", name="service_non_negative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    route_id: Mapped[str] = mapped_column(ForeignKey("van_routes.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)  # порядок в рейсе, с 0
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"))
    eta_min: Mapped[int] = mapped_column(Integer)  # прибытие
    service_min: Mapped[int] = mapped_column(Integer, default=0)  # стоянка: выгрузка коробок
    base_drop: Mapped[int] = mapped_column(Integer, default=0)  # посылок из хаба для этого ПВЗ
    added: Mapped[bool] = mapped_column(Boolean, default=False)  # остановку добавил mesh
