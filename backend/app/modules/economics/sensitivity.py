"""Стресс-тесты на одну пригодную единицу при изменении одного параметра.
Расчёт использует неокруглённые потоки N8. Доля B усредняет дополнительные расходы.
Границы ищем по знаку эффекта до округления; равенство означает выбор хаба.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal

from app.modules.economics.params import DEFAULTS, ModelParams, kopecks
from app.modules.economics.schemas import (
    BoundaryRule,
    Distribution,
    ParameterBoundary,
    Sensitivity,
    StressParameter,
    StressPoint,
)
from app.modules.economics.service import sale_profile, unit_balance


@dataclass(frozen=True)
class Axis:
    unit: str
    start: int | Decimal
    stop: int | Decimal
    whole: bool = False
    fraction: bool = False


PRECISION = Decimal("0.000001")
# Интервалы графика — сценарии для показа. При поиске нуля расширяем их отдельно.
AXES = {
    StressParameter.DEMAND: Axis("ед./сут", 0, 2 * DEFAULTS.demand_per_day),
    StressParameter.DAYS: Axis("сут", 0, DEFAULTS.month_days, whole=True),
    StressParameter.VOLUME: Axis("л", DEFAULTS.volume_l / 2, 5 * DEFAULTS.volume_l),
    StressParameter.OPENED: Axis("доля 0–1", 0, 1, fraction=True),
    StressParameter.REPACK: Axis("коп/ед.", 0, 2 * kopecks(DEFAULTS.repack_cost), whole=True),
    StressParameter.TARIFF: Axis("коп/(л·сут)", 0, 2 * DEFAULTS.storage_tariff, whole=True),
    StressParameter.LOSS: Axis("доля 0–1", 0, 10 * DEFAULTS.loss_share, fraction=True),
    StressParameter.SEASON: Axis("коэффициент", DEFAULTS.season_factor, 2 * DEFAULTS.season_factor),
}


@dataclass(frozen=True)
class Scenario:
    demand_per_day: Decimal
    shelf_days: Decimal
    volume_l: Decimal
    opened_share: Decimal
    repack_cost_kop: Decimal
    storage_tariff_kop_l_day: Decimal
    loss_share: Decimal
    season_factor: Decimal

    @classmethod
    def baseline(cls, p: ModelParams) -> "Scenario":
        return cls(
            p.demand_per_day,
            Decimal(p.shelf_days),
            p.volume_l,
            p.opened_share,
            p.repack_cost,
            Decimal(p.storage_tariff),
            p.loss_share,
            p.season_factor,
        )


@dataclass(frozen=True)
class Effect:
    chain: Decimal
    after_storage_fee: Decimal
    pvz: Decimal
    seller: Decimal


def evaluate(s: Scenario, p: ModelParams) -> Effect:
    profile = sale_profile(s.demand_per_day, s.shelf_days, p)
    base = unit_balance("A", s.volume_l, profile, "mp", p)
    # N8 для A уже включает штатное плечо. Здесь только сезонная надбавка к транспорту.
    extra_van = profile.via_van * p.van_space_kop * (s.season_factor - 1)
    repack = s.opened_share * s.repack_cost_kop
    chain = base.effect - repack - extra_van - s.loss_share * p.replacement_kop
    # При изменении договорного тарифа труд и аренда фиксированы, разницу получает ПВЗ.
    volume_days = profile.days * s.volume_l
    fee_delta = volume_days * (s.storage_tariff_kop_l_day - p.storage_tariff)
    storage_margin = base.storage_margin + fee_delta
    pvz = base.pvz + s.opened_share * p.repack_fee_kop - repack + fee_delta
    return Effect(chain, chain - storage_margin, pvz, base.seller)


def sensitivity(
    param: StressParameter,
    start: Decimal | None,
    stop: Decimal | None,
    steps: int | None,
    p: ModelParams = DEFAULTS,
) -> Sensitivity:
    axis = AXES[param]
    low = Decimal(axis.start) if start is None else start
    high = Decimal(axis.stop) if stop is None else stop
    if low < 0 or high <= low or (axis.fraction and high > 1):
        raise ValueError("Нужен возрастающий неотрицательный диапазон, доли задаются от 0 до 1.")
    if param in (StressParameter.VOLUME, StressParameter.SEASON) and low <= 0:
        raise ValueError("Объём и сезонный множитель должны быть положительными.")
    if axis.whole and (low != int(low) or high != int(high)):
        raise ValueError("Для дней и денежных ставок границы должны быть целыми.")
    if steps is None:
        steps = min(21, int(high - low) + 1) if axis.whole else 21
    if steps < 2 or (axis.whole and steps > high - low + 1):
        raise ValueError("Число точек должно помещаться в заданный диапазон.")
    baseline = Scenario.baseline(p)
    points = []
    for i in range(steps):
        x = low + (high - low) * i / (steps - 1)
        if axis.whole:
            x = kopecks(x)
        e = evaluate(replace(baseline, **{param.value: Decimal(x)}), p)
        chain, pvz, seller = kopecks(e.chain), kopecks(e.pvz), kopecks(e.seller)
        points.append(
            StressPoint(
                x=x,
                effect_kop=chain,
                mesh_profitable=e.chain > 0,
                effect_after_storage_fee_kop=kopecks(e.after_storage_fee),
                distribution=Distribution(
                    pvz_kop=pvz, seller_kop=seller, mp_kop=chain - pvz - seller
                ),
            )
        )
    base_value = getattr(baseline, param.value)
    return Sensitivity(
        param=param,
        unit=axis.unit,
        base_value=int(base_value) if axis.whole else base_value,
        points=points,
    )


def _rule(
    value: Callable[[int | Decimal], Decimal],
    low: int | Decimal,
    high: int | Decimal,
    unit: str,
    whole: bool,
) -> BoundaryRule:
    """Две соседние точки отделяют прибыльный сценарий от хаба даже при скачке в 1 копейку."""
    low_mesh, high_mesh = value(low) > 0, value(high) > 0
    if low_mesh == high_mesh:
        return BoundaryRule(
            threshold=None,
            mesh_edge=None,
            hub_edge=None,
            rule=(
                "Mesh выгоден во всём проверенном диапазоне."
                if low_mesh
                else "Во всём проверенном диапазоне выбираем хаб."
            ),
        )
    tick = 1 if whole else PRECISION
    while high - low > tick:
        mid = ((low + high) // (2 * tick)) * tick
        if (value(mid) > 0) == low_mesh:
            low = mid
        else:
            high = mid
    mesh_edge, hub_edge = (low, high) if low_mesh else (high, low)
    relation = "≤" if low_mesh else "≥"
    return BoundaryRule(
        threshold=hub_edge if value(hub_edge) == 0 else mesh_edge,
        mesh_edge=mesh_edge,
        hub_edge=hub_edge,
        rule=f"Mesh выгоден при значении {relation} {mesh_edge} {unit}, иначе хаб.",
    )


def boundaries(p: ModelParams = DEFAULTS) -> list[ParameterBoundary]:
    baseline = Scenario.baseline(p)
    results = []
    for param, axis in AXES.items():

        def at(x: int | Decimal, param: StressParameter = param) -> Effect:
            return evaluate(replace(baseline, **{param.value: Decimal(x)}), p)

        low = PRECISION if param in (StressParameter.VOLUME, StressParameter.SEASON) else 0
        high = 1 if axis.fraction else axis.stop
        first, last = at(low), at(high)
        # Обе функции монотонны при фиксированных остальных параметрах. Расширяем интервал,
        # только пока эффект движется к нулю с той же стороны. Верхний предел — 40 удвоений.
        for _ in range(40):
            if axis.fraction or not any(
                a * b > 0 and abs(b) < abs(a)
                for a, b in (
                    (first.chain, last.chain),
                    (first.after_storage_fee, last.after_storage_fee),
                )
            ):
                break
            first, high = last, high * 2
            last = at(high)
        results.append(
            ParameterBoundary(
                param=param,
                unit=axis.unit,
                search_from=low,
                search_to=high,
                chain=_rule(lambda x: at(x).chain, low, high, axis.unit, axis.whole),
                after_storage_fee=_rule(
                    lambda x: at(x).after_storage_fee, low, high, axis.unit, axis.whole
                ),
            )
        )
    return results
