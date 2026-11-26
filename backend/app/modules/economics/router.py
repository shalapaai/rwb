"""Справочник допущений и расчёт экономики отказа для всех ролей.
Расчёты охватывают единицу отказа, стресс-сценарии и месяц работы ПВЗ.
Они не создают операций с остатками или начислений участникам.
"""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import current_role
from app.modules.economics.params import DEFAULT_REPACK_PAYER, DEFAULTS, catalog
from app.modules.economics.pvz import pvz_month
from app.modules.economics.schemas import (
    Grade,
    Parameter,
    ParameterBoundary,
    PvzMonth,
    RepackPayer,
    Sensitivity,
    StressParameter,
    UnitEconomics,
)
from app.modules.economics.sensitivity import boundaries, sensitivity
from app.modules.economics.service import unit_economics
from app.modules.routing.generator import RobustnessReport, robustness_report

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


RangeLimit = Annotated[Decimal | None, Query(max_digits=15, decimal_places=6)]


@router.get("/sensitivity", summary="Эффект и выгода сторон при изменении одного параметра")
def model_sensitivity(
    param: StressParameter = StressParameter.DEMAND,
    start: Annotated[Decimal | None, Query(alias="from", max_digits=15, decimal_places=6)] = None,
    to: RangeLimit = None,
    steps: Annotated[int | None, Query(ge=2, le=501)] = None,
) -> Sensitivity:
    try:
        return sensitivity(param, start, to, steps)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/boundary", summary="Границы выбора mesh при остальных базовых условиях")
def model_boundary() -> list[ParameterBoundary]:
    return boundaries()


@router.get("/pvz-economics", summary="Доход, затраты и безубыточность ПВЗ за месяц")
def model_pvz_economics(
    share: Annotated[
        int, Query(ge=10, le=50, description="Доля склада под mesh, %.")
    ] = DEFAULTS.mesh_share_pct,
    utilization: Annotated[
        Decimal,
        Query(
            ge=0,
            le=1,
            max_digits=7,
            decimal_places=6,
            description="Доля занятых литров на mesh-полке, от 0 до 1.",
        ),
    ] = DEFAULTS.utilization,
) -> PvzMonth:
    return pvz_month(share, utilization)


@router.get("/robustness", summary="Устойчивость трёх схем доставки на 20 синтетических городах")
def model_robustness() -> RobustnessReport:
    return robustness_report()
