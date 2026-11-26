"""Ожидаемая стоимость следующей продажи одной пригодной отказанной единицы.
Спрос моделируется пуассоновским потоком до вывоза с полки в конце срока D.
Расчёт сравнивает затраты до готовности к выдаче. Упущенные продажи в пути не оцениваются.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.modules.economics.params import DEFAULTS, ModelParams, kopecks
from app.modules.economics.schemas import (
    CostItem,
    Distribution,
    Grade,
    RepackPayer,
    Scheme,
    UnitEconomics,
)


class Breakdown:
    """Сохраняет дробные копейки до представления накопительной суммы в ответе."""

    def __init__(self) -> None:
        self.total = Decimal(0)
        self.items: list[CostItem] = []

    def add(self, label: str, formula: str, value: Decimal | int, **inputs: Decimal | int) -> None:
        previous = kopecks(self.total)
        self.total += value
        # Разность округлённых итогов распределяет копейки без отдельной фиктивной статьи.
        self.items.append(
            CostItem(
                label=label,
                formula=formula,
                inputs=inputs,
                amount_kop=kopecks(self.total) - previous,
            )
        )

    def scheme(self) -> Scheme:
        return Scheme(items=self.items, total_kop=kopecks(self.total))


@dataclass(frozen=True)
class SaleProfile:
    sold: Decimal
    expired: Decimal
    days: Decimal
    via_van: Decimal
    van_share: Decimal


def sale_profile(demand: Decimal, shelf_days: Decimal | int, params: ModelParams) -> SaleProfile:
    expired = (-demand * shelf_days).exp()
    sold = 1 - expired
    days = sold / demand if demand else Decimal(shelf_days)
    van_share = Decimal(params.van_units) / (params.van_units + params.own_units)
    return SaleProfile(sold, expired, days, sold * van_share, van_share)


@dataclass(frozen=True)
class UnitBalance:
    base: Breakdown
    mesh: Breakdown
    pvz: Decimal
    seller: Decimal
    storage_cost: Decimal
    storage_margin: Decimal

    @property
    def effect(self) -> Decimal:
        return self.base.total - self.mesh.total


def unit_balance(
    grade: Grade,
    volume_l: Decimal,
    profile: SaleProfile,
    repack_payer: RepackPayer,
    params: ModelParams,
) -> UnitBalance:
    sold, expired, days = profile.sold, profile.expired, profile.days
    van_share, via_van = profile.van_share, profile.via_van
    litre_days = volume_l * days

    base = Breakdown()
    base.add("Возврат отказа в хаб", "C_ret", params.hub_return_kop, C_ret=params.hub_return_kop)
    base.add("Следующий заказ из хаба", "C_hub", params.hub_order_kop, C_hub=params.hub_order_kop)

    mesh = Breakdown()
    inspection = params.labour(params.inspection_min)
    mesh.add(
        "Проверка пригодности",
        "w × t / 60",
        inspection,
        w=params.wage_kop_hour,
        t=params.inspection_min,
    )
    repack_cost = Decimal(0)
    repack_fee = 0
    if grade == "B":
        repack_cost = params.repack_cost
        repack_fee = params.repack_fee_kop
        mesh.add(
            "Переупаковка B",
            "w × t / 60 + пакет",
            repack_cost,
            w=params.wage_kop_hour,
            t=params.repack_min,
            пакет=params.pack_kop,
        )

    storage_cost = litre_days * (params.space_kop_l_day + params.shelf_labour_kop_l_day)
    mesh.add(
        "Хранение до продажи или вывоза",
        "V × ожидаемые_сутки × (s_место + s_труд)",
        storage_cost,
        V=volume_l,
        ожидаемые_сутки=days,
        s_место=params.space_kop_l_day,
        s_труд=params.shelf_labour_kop_l_day,
    )
    mesh.add(
        "Отбор и ИТ для местной продажи",
        "p × (w × t / 60 + ИТ)",
        sold * params.own_cost,
        p=sold,
        w=params.wage_kop_hour,
        t=params.pick_min,
        ИТ=params.it_kop,
    )
    mesh.add(
        "Попутное плечо и приёмка",
        "p × α × (место + w × t / 60)",
        via_van * params.move_cost,
        p=sold,
        α=van_share,
        место=params.van_space_kop,
        w=params.wage_kop_hour,
        t=params.receive_min,
    )
    mesh.add(
        "Вывоз остатка и следующий заказ из хаба",
        "(1 − p) × (C_ret + C_hub)",
        expired * base.total,
        p=sold,
        C_ret=params.hub_return_kop,
        C_hub=params.hub_order_kop,
    )

    storage_margin = litre_days * params.shelf_profit_kop_l_day
    # Вклад всех ПВЗ: тарифы за операции минус их труд и расходники, плюс маржа хранения.
    pvz = (
        params.inspection_fee_kop
        - inspection
        + repack_fee
        - repack_cost
        + storage_margin
        + sold * (params.pick_fee_kop - params.labour(params.pick_min))
        + via_van * (params.receive_fee_kop - params.labour(params.receive_min))
    )
    seller = sold * params.seller_saving_kop
    if repack_payer == "seller":
        seller -= repack_fee
    return UnitBalance(base, mesh, pvz, seller, storage_cost, storage_margin)


def unit_economics(
    *,
    grade: Grade,
    volume_l: Decimal,
    demand_per_day: Decimal,
    shelf_days: int,
    repack_payer: RepackPayer,
    params: ModelParams = DEFAULTS,
) -> UnitEconomics:
    """Обе схемы исполняют одну следующую продажу, в том числе после вывоза в хаб."""
    if grade not in ("A", "B") or repack_payer not in ("mp", "seller"):
        raise ValueError("Неизвестная оценка или плательщик")
    if demand_per_day < 0 or volume_l <= 0 or shelf_days < 0:
        raise ValueError("Проверьте спрос, объём и срок хранения")
    profile = sale_profile(demand_per_day, shelf_days, params)
    balance = unit_balance(grade, volume_l, profile, repack_payer, params)
    base, mesh = balance.base, balance.mesh
    local_delivery_min = (
        1 - profile.van_share
    ) * params.own_delivery_min + profile.van_share * params.van_delivery_min
    effect = kopecks(base.total) - kopecks(mesh.total)
    pvz_kop, seller_kop = kopecks(balance.pvz), kopecks(balance.seller)
    return UnitEconomics(
        grade=grade,
        volume_l=volume_l,
        demand_per_day=demand_per_day,
        shelf_days=shelf_days,
        repack_payer=repack_payer,
        sale_probability=profile.sold,
        expected_days=profile.days,
        base=base.scheme(),
        mesh=mesh.scheme(),
        local_delivery_min=local_delivery_min,
        hub_delivery_min=params.hub_delivery_min,
        storage_payment_kop=kopecks(balance.storage_cost + balance.storage_margin),
        effect_after_storage_fee_kop=kopecks(balance.effect - balance.storage_margin),
        effect_kop=effect,
        distribution=Distribution(
            pvz_kop=pvz_kop,
            seller_kop=seller_kop,
            mp_kop=effect - pvz_kop - seller_kop,
        ),
    )
