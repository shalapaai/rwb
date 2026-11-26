"""Справочник допущений и расчёт экономики отказа для всех ролей.
Оба запроса читают синтетический сценарий и возвращают расчёт на одну единицу.
Они не создают операций с остатками или начислений участникам.
"""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.auth import current_role
from app.modules.economics.params import DEFAULT_REPACK_PAYER, DEFAULTS, catalog
from app.modules.economics.schemas import Grade, Parameter, RepackPayer, UnitEconomics
from app.modules.economics.service import unit_economics

router = APIRouter(prefix="/model", tags=["economics"], dependencies=[Depends(current_role)])


@router.get("/params", summary="Синтетические допущения v3 и их основания")
def model_params() -> list[Parameter]:
    return catalog()


@router.get("/unit-economics", summary="Стоимость следующей продажи пригодного отказа")
def model_unit_economics(
    grade: Grade = "A",
    volume_l: Annotated[Decimal, Query(gt=0, max_digits=12, decimal_places=6)] = DEFAULTS.volume_l,
    demand_per_day: Annotated[
        Decimal, Query(ge=0, max_digits=12, decimal_places=6)
    ] = DEFAULTS.demand_per_day,
    shelf_days: Annotated[
        int, Query(ge=0, description="Срок хранения до вывоза, сутки.")
    ] = DEFAULTS.shelf_days,
    repack_payer: Annotated[
        RepackPayer, Query(description="Сценарий оплаты B. Можно сравнить МП и продавца.")
    ] = DEFAULT_REPACK_PAYER,
) -> UnitEconomics:
    return unit_economics(
        grade=grade,
        volume_l=volume_l,
        demand_per_day=demand_per_day,
        shelf_days=shelf_days,
        repack_payer=repack_payer,
    )
