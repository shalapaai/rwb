"""Публичные типы расчёта на одну пригодную отказанную единицу.
Стоимость каждой статьи и результат участника выражены в целых копейках.
Вероятность и срок сохраняют точность промежуточного расчёта.
"""

from decimal import Decimal
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
