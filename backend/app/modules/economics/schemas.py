"""Публичные типы расчёта на одну пригодную отказанную единицу.
Стоимость каждой статьи и результат участника выражены в целых копейках.
Вероятность и срок сохраняют точность промежуточного расчёта.
"""

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

Grade = Literal["A", "B"]
RepackPayer = Literal["mp", "seller"]


class Parameter(BaseModel):
    id: str
    label: str
    value: int | Decimal
    unit: str
    basis: str
    source: str | None = None


class CostItem(BaseModel):
    label: str
    formula: str
    inputs: dict[str, int | Decimal]
    amount_kop: int


class Scheme(BaseModel):
    items: list[CostItem]
    total_kop: int


class Distribution(BaseModel):
    pvz_kop: int = Field(description="Оплата операций и хранения минус расходы всех ПВЗ.")
    seller_kop: int = Field(description="Снижение платы за доставку за вычетом доплат продавца.")
    mp_kop: int = Field(description="Остаток эффекта цепочки после расчётов с ПВЗ и продавцом.")


class UnitEconomics(BaseModel):
    grade: Grade
    volume_l: Decimal
    demand_per_day: Decimal
    shelf_days: int
    repack_payer: RepackPayer
    sale_probability: Decimal = Field(
        description="1 − exp(−λD), где λ — спрос в сутки, D — срок до вывоза."
    )
    expected_days: Decimal = Field(
        description="p / λ суток на полке, при нулевом спросе — D суток."
    )
    base: Scheme
    mesh: Scheme
    local_delivery_min: Decimal = Field(
        description="Срок при продаже в городе: (1−α) × срок своей полки + α × срок газели."
    )
    hub_delivery_min: int = Field(description="Срок исполнения следующего заказа из хаба.")
    storage_payment_kop: int = Field(description="Выплата МП за хранение: место, труд и маржа ПВЗ.")
    effect_after_storage_fee_kop: int = Field(
        description="Расчёт по листу «Модель v3»: p·G − c_grade − s·V·E[t], с полным тарифом s."
    )
    effect_kop: int = Field(
        description="Эффект цепочки с учётом того, что маржу хранения получает ПВЗ."
    )
    distribution: Distribution


class StressParameter(StrEnum):
    DEMAND = "demand_per_day"
    DAYS = "shelf_days"
    VOLUME = "volume_l"
    OPENED = "opened_share"
    REPACK = "repack_cost_kop"
    TARIFF = "storage_tariff_kop_l_day"
    LOSS = "loss_share"
    SEASON = "season_factor"


class StressPoint(BaseModel):
    x: int | Decimal
    mesh_profitable: bool = Field(description="Эффект цепочки положителен до округления.")
    effect_kop: int
    effect_after_storage_fee_kop: int
    distribution: Distribution


class Sensitivity(BaseModel):
    param: StressParameter
    unit: str
    base_value: int | Decimal
    points: list[StressPoint]


class BoundaryRule(BaseModel):
    threshold: int | Decimal | None = Field(
        description="Нуль, если он представим, иначе ближайшее значение со стороны mesh."
    )
    mesh_edge: int | Decimal | None
    hub_edge: int | Decimal | None
    rule: str


class ParameterBoundary(BaseModel):
    param: StressParameter
    unit: str
    search_from: int | Decimal
    search_to: int | Decimal
    chain: BoundaryRule
    after_storage_fee: BoundaryRule


class PvzMonth(BaseModel):
    share: int
    utilization: Decimal
    shelf_l: Decimal
    occupied_l: Decimal
    storage_tariff_kop_l_day: int
    storage_income_kop: int
    operations_income_kop: int
    income_kop: int
    space_cost_kop: int
    labour_cost_kop: int
    materials_cost_kop: int
    cost_kop: int
    profit_kop: int
    break_even_utilization: Decimal | None = Field(
        description="Доля занятых литров, при которой прибыль равна нулю. "
        "Значение свыше 1 недостижимо, null означает отсутствие положительного вклада."
    )
