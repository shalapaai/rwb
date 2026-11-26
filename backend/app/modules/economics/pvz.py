"""Экономика выделенной mesh-полки одного типового ПВЗ за расчётный месяц.
Плата за хранение пропорциональна занятым литро-дням, аренда — всей выделенной полке.
Поток пригодных отказов восстанавливается из среднего запаса и времени на полке.
"""

from decimal import Decimal

from app.modules.economics.params import DEFAULTS, ModelParams, kopecks
from app.modules.economics.schemas import PvzMonth
from app.modules.economics.service import sale_profile


def pvz_month(share: int, utilization: Decimal, p: ModelParams = DEFAULTS) -> PvzMonth:
    profile = sale_profile(p.pvz_demand_per_day, p.shelf_days, p)
    shelf = Decimal(p.storage_total_l) * share / 100
    full_litre_days = shelf * p.month_days
    # При постоянном потоке: число поступлений = запас в единицах × месяц / среднее лежание.
    arrivals_at_full = full_litre_days / (p.volume_l * profile.days)
    fees_per_arrival = (
        p.inspection_fee_kop
        + p.opened_share * p.repack_fee_kop
        + profile.sold * p.pick_fee_kop
        + profile.via_van * p.receive_fee_kop
    )
    minutes_per_arrival = (
        p.inspection_min
        + p.opened_share * p.repack_min
        + profile.sold * p.pick_min
        + profile.via_van * p.receive_min
    )
    storage_at_full = full_litre_days * p.storage_tariff
    fees_at_full = arrivals_at_full * fees_per_arrival
    labour_at_full = full_litre_days * p.shelf_labour_kop_l_day + arrivals_at_full * p.labour(
        minutes_per_arrival
    )
    materials_at_full = arrivals_at_full * p.opened_share * p.pack_kop
    rent = full_litre_days * p.space_kop_l_day
    contribution = storage_at_full + fees_at_full - labour_at_full - materials_at_full

    storage_income = kopecks(storage_at_full * utilization)
    income = kopecks((storage_at_full + fees_at_full) * utilization)
    labour = kopecks(labour_at_full * utilization)
    cost = kopecks(rent + (labour_at_full + materials_at_full) * utilization)
    return PvzMonth(
        share=share,
        utilization=utilization,
        shelf_l=shelf,
        occupied_l=shelf * utilization,
        storage_tariff_kop_l_day=p.storage_tariff,
        storage_income_kop=storage_income,
        operations_income_kop=income - storage_income,
        income_kop=income,
        space_cost_kop=kopecks(rent),
        labour_cost_kop=labour,
        materials_cost_kop=cost - kopecks(rent) - labour,
        cost_kop=cost,
        profit_kop=income - cost,
        break_even_utilization=rent / contribution if contribution > 0 else None,
    )
