"""Запросы и ответы приёма отказа и mesh-полки."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class RefusalIn(BaseModel):
    sku_id: str
    grade: Literal["A", "B", "C"]  # A не вскрыт, B упаковку восстановил, C не восстановить
    order_id: int | None = None
    barcode: str | None = None  # если не задан — сгенерируем
    comment: str = ""


class RefusalOut(BaseModel):
    unit_id: int
    barcode: str
    placement: Literal["shelf", "queue", "return_queue", "hub"]
    decision: Literal["shelf", "repack", "hub"]
    reason: str
    used_l: float
    limit_l: float
    txn_ids: list[int]  # проводки за проверку и переупаковку


class SettingsIn(BaseModel):
    mesh_share_pct: int = Field(ge=10, le=50)  # доля склада ПВЗ под mesh, выбирает ПВЗ


class SettingsOut(BaseModel):
    estimated_days: int | None = None
    pending_share_pct: int | None = None
    reason: str | None = None
    pvz_id: str
    mesh_share_pct: float
    used_l: float
    limit_l: float
    over_limit: bool


class StockUnit(BaseModel):
    unit_id: int
    barcode: str
    sku: str
    status: str
    grade: str | None
    volume_l: float
    shelved_at: str | None
    age_days: int


class ProductOption(BaseModel):
    sku: str
    name: str
    volume_l: float


class WriteOffOut(ProductOption):
    unit_id: int
    barcode: str
    category: Literal["packaging", "volume"]
    reason: str


class StockOut(BaseModel):
    writeoffs_today: list[WriteOffOut]
    simulation_day: date
    estimated_days: int | None = None
    products: list[ProductOption]
    pending_share_reason: str | None = None
    pending_share_pct: int | None = None
    pvz_id: str
    mesh_share_pct: float
    used_l: float
    limit_l: float
    units: list[StockUnit]


class ShelfTask(BaseModel):
    scheduled_for: date | None = None
    blocked_reason: str | None = None
    unit_id: int
    version: int
    sku: str
    barcode: str
    volume_l: float
    action: Literal["remove", "return", "shelve", "reduce"]


class ShelfActionOut(BaseModel):
    unit_id: int
    status: Literal["to_hub", "on_shelf", "remove_pending"]


class ReductionSummary(BaseModel):
    day: date
    sent_l: float
    limit_l: float
    target_l: float
