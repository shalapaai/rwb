"""Запросы и ответы заказов и заданий ПВЗ."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.modules.inventory.schemas import ReductionSummary, ShelfTask


class OrderIn(BaseModel):
    sku_id: str
    pvz_id: str  # ПВЗ выдачи
    accepts_neighbor: bool = False  # согласен забрать в соседнем ПВЗ
    qty: int = Field(default=1, ge=1)


class VersionIn(BaseModel):
    version: int  # версия, которую видел оператор; не совпала — 409


class ReturnIn(VersionIn):
    grade: Literal["A", "B", "C"]  # оценка фиксируется, но возврат всё равно едет в хаб


class OrderOut(BaseModel):
    id: int
    sku: str
    qty: int
    pvz_id: str
    status: str
    version: int
    variant: str | None
    source_node: str | None
    cost_kop: int | None
    eta_h: float | None
    candidates: list[dict[str, Any]]  # все варианты с ценой, сроком и причиной
    created_at: datetime


class TaskRow(BaseModel):
    order_id: int
    version: int
    sku: str
    qty: int
    status: str
    variant: str | None
    source_node: str | None
    pvz_id: str
    van: str | None
    van_eta_min: int | None


class TasksOut(BaseModel):
    reduction: ReductionSummary | None = None
    pvz_id: str
    shelf: list[ShelfTask]
    to_pick: list[TaskRow]
    to_load: list[TaskRow]
    to_receive: list[TaskRow]
    to_issue: list[TaskRow]
