"""Категории товаров и дневная история продаж в ПВЗ.

По категории можно узнать причину запрета на mesh-хранение.
Продажи измеряются в штуках и суммируются по товару, ПВЗ и дню.
"""

from datetime import date

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    mesh_allowed: Mapped[bool] = mapped_column(Boolean)
    ban_reason: Mapped[str | None] = mapped_column(Text)
    marked: Mapped[bool] = mapped_column(Boolean)


class Sale(Base):
    __tablename__ = "sales"
    __table_args__ = (CheckConstraint("qty > 0", name="sales_qty_positive"),)

    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"), primary_key=True)
    pvz_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    qty: Mapped[int] = mapped_column(Integer)
