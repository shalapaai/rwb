"""Запросы и ответы приёма отказа и mesh-полки."""

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
    placement: Literal["shelf", "hub"]
    decision: Literal["shelf", "repack", "hub"]
    reason: str
    used_l: float
    limit_l: float
    txn_ids: list[int]  # проводки за проверку и переупаковку


class SettingsIn(BaseModel):
    mesh_share_pct: int = Field(ge=10, le=50)  # доля склада ПВЗ под mesh, выбирает ПВЗ


class SettingsOut(BaseModel):
    pvz_id: str
    mesh_share_pct: int
    used_l: float
    limit_l: float
    over_limit: bool  # полка уже заполнена больше нового лимита: новые отказы уйдут в хаб


class StockUnit(BaseModel):
    unit_id: int
    barcode: str
    sku: str
    status: str
    grade: str | None
    volume_l: float
    shelved_at: str | None
    age_days: int


class StockOut(BaseModel):
    pvz_id: str
    mesh_share_pct: int
    used_l: float
    limit_l: float
    units: list[StockUnit]
