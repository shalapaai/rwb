"""Синтетические допущения v3 для одной пригодной отказанной единицы.
Рыночные ориентиры проверены в ноябре 2026 года и сохранены с единицами и источником.
Тарифы модели задают сценарий пилота. Для денежных ставок единица хранения — копейка.
"""

from dataclasses import dataclass, field, fields
from decimal import ROUND_HALF_UP, Decimal

from app.modules.economics.schemas import Parameter, RepackPayer
from app.modules.routing.models import Params

DOC = "docs/02-business-model.md"
AUDIT = "docs/08-metrics-audit.md"
ROADMAP = "docs/roadmap-26-11.xlsx"
WB = "https://seller.wildberries.ru/instructions/ru/ru/material/"
KOPECK = "коп/ед."
# Команда утвердила оплату переупаковки маркетплейсом для базового сценария N8.
DEFAULT_REPACK_PAYER: RepackPayer = "mp"


def assumption[T: (int, Decimal)](value: T, label: str, unit: str, basis: str, source: str) -> T:
    return field(default=value, metadata=dict(label=label, unit=unit, basis=basis, source=source))


def kopecks(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class ModelParams:
    hub_return_kop: int = assumption(
        3700,
        "Возврат отказа в хаб с обработкой",
        KOPECK,
        "Оценка v1 для обратной логистики. Требуется замер по обратному рейсу.",
        DOC,
    )
    hub_order_kop: int = assumption(
        6040,
        "Следующий городской заказ из хаба",
        KOPECK,
        "Сценарный C_hub из листа «Модель v3» для малого товара. В v2 агрегат включал "
        "привоз партии продавцом. Для повторной продажи нужен отдельный замер прямого плеча.",
        AUDIT,
    )
    wage_kop_hour: int = assumption(
        33000,
        "Полная стоимость часа оператора",
        "коп/ч",
        "Lamoda, ПВЗ на Советской, 147, Йошкар-Ола: 42–51 тыс. ₽/мес до вычета. "
        "При условных 168 ч это 250–304 ₽/ч. Сценарий с расходами работодателя — 330 ₽/ч.",
        "https://yoshkar-ola.hh.ru/employer/780654",
    )
    inspection_min: int = assumption(
        3,
        "Проверка пригодности A и B",
        "мин/ед.",
        "Норматив v2. Замерить на пилоте.",
        DOC,
    )
    repack_min: int = assumption(
        2,
        "Дополнительная работа с упаковкой B",
        "мин/ед.",
        "Сценарий простой замены пакета на малом товаре. Замерить на пилоте.",
        ROADMAP,
    )
    pack_kop: int = assumption(
        306,
        "Один пакет для B",
        KOPECK,
        "ЕКАПАК, 21.10.2026: пакет 250×350 мм, 50 мкм. 4 284 ₽ за 1 400 шт. без доставки.",
        "https://ekapak.ru/product/kurerskii-paket-25035040mm-50mkm-belyi-1001400st",
    )
    space_kop_l_day: int = assumption(
        5,
        "Место на занятой полке",
        "коп/(л·сут)",
        "900 ₽/м² в месяц × 1,2 м² / 720 полезных л / 30 сут = 0,05 ₽/л/сут. "
        "При неполной загрузке удельная стоимость выделенной площади выше.",
        DOC,
    )
    shelf_labour_kop_l_day: int = assumption(
        5,
        "Труд по хранению и сверке остатков",
        "коп/(л·сут)",
        "Допущение: 0,05 ₽ × 720 л = 36 ₽ в сутки, около 6,5 мин при 330 ₽/ч. "
        "Входит только обслуживание полки между приёмом и отбором.",
        ROADMAP,
    )
    shelf_profit_kop_l_day: int = assumption(
        5,
        "Маржа ПВЗ на хранении",
        "коп/(л·сут)",
        "Из тарифа v2 15 коп. после 5 коп. за место и 5 коп. за труд остаётся 5 коп. "
        "Ориентир для малого возврата FBS на склад: 0,07 ₽/л/сут × коэффициент хранения.",
        WB + "fixation-of-tariffs",
    )
    pick_min: Decimal = assumption(
        Decimal("1.5"),
        "Отбор для местного заказа",
        "мин/ед.",
        "Норматив v2. Одинаковый отбор со своей полки и к газели.",
        DOC,
    )
    receive_min: Decimal = assumption(
        Decimal("0.75"),
        "Приёмка из газели",
        "мин/ед.",
        "Норматив v2.",
        DOC,
    )
    it_kop: int = assumption(
        100,
        "ИТ на местный заказ",
        KOPECK,
        "Синтетическая оценка ИТ и сканирования из v2.",
        DOC,
    )
    van_space_kop: int = assumption(
        400,
        "Место на попутном рейсе между штатными остановками",
        KOPECK,
        "Остаточная оценка: 7,10 ₽ v2 минус 0,75 мин при прежних 250 ₽/ч ≈ 4 ₽. "
        "Труд приёмки считается отдельно. Дополнительный крюк в этот сценарий не входит.",
        AUDIT,
    )
    van_units: int = assumption(
        14,
        "Единицы газелью в ночном демо",
        "ед.",
        "Ночной план: 14 газелью, 3 со своей полки, 2 через хаб. Выборка одного дня.",
        AUDIT,
    )
    own_units: int = assumption(
        3,
        "Единицы со своей полки в ночном демо",
        "ед.",
        "Для условной доли газели среди местных продаж берём 14 / (14 + 3).",
        AUDIT,
    )
    inspection_fee_kop: int = assumption(
        1500,
        "Выплата ПВЗ за оценку",
        KOPECK,
        "Внутренний тариф v2 за проверку возврата.",
        DOC,
    )
    repack_fee_kop: int = assumption(
        1500,
        "Выплата ПВЗ за переупаковку B",
        KOPECK,
        "Синтетическая компенсация сверх проверки: 11 ₽ труда + 3,06 ₽ пакет + 0,94 ₽ маржи.",
        ROADMAP,
    )
    pick_fee_kop: int = assumption(
        900,
        "Выплата ПВЗ за отбор",
        KOPECK,
        "Внутренний тариф v2 за отбор.",
        DOC,
    )
    receive_fee_kop: int = assumption(
        400,
        "Выплата ПВЗ за приём из газели",
        KOPECK,
        "Внутренний тариф v2 за приём.",
        DOC,
    )
    seller_saving_kop: int = assumption(
        800,
        "Скидка продавцу за местное исполнение",
        KOPECK,
        "В проекте 60 → 52 ₽. Для сравнения: в примере WB для 2 л база 46 + 14 ₽ "
        "до коэффициентов. Скидка 8 ₽ — допущение пилота на один местный заказ.",
        WB + "logistics-types-and-cost-calculation",
    )
    shelf_days: int = assumption(
        14,
        "Срок хранения отказа до вывоза",
        "сут",
        "Решение команды для базового сценария N8. Срок можно менять для сравнения.",
        "Решение команды по N8",
    )
    volume_l: Decimal = assumption(
        Decimal(2),
        "Объём типовой единицы",
        "л",
        "Термокружка SKU-001 из демо.",
        DOC,
    )
    demand_per_day: Decimal = assumption(
        Decimal(1),
        "Доступный этой единице городской спрос",
        "ед./сут",
        "Ходовой SKU из docs/02. В формуле Пуассона это интенсивность подходящих заказов "
        "для первой единицы в очереди. Для пилота нужен прогноз по SKU.",
        DOC,
    )

    def labour(self, minutes: int | Decimal) -> Decimal:
        return self.wage_kop_hour * Decimal(minutes) / 60

    @property
    def own_cost(self) -> Decimal:
        return self.labour(self.pick_min) + self.it_kop

    @property
    def move_cost(self) -> Decimal:
        return self.labour(self.receive_min) + self.van_space_kop


DEFAULTS = ModelParams()


def catalog(params: ModelParams = DEFAULTS) -> list[Parameter]:
    return [
        Parameter(id=f.name, value=getattr(params, f.name), **f.metadata) for f in fields(params)
    ]


def routing_params(params: ModelParams = DEFAULTS) -> Params:
    """Цена исполнения после заказа. Предыдущая оценка и хранение уже оплачены."""
    return Params(
        own_kop=kopecks(params.own_cost),
        move_kop=kopecks(params.move_cost),
        transfer_kop=kopecks(params.labour(params.receive_min + params.pick_min)),
        hub_kop=params.hub_order_kop,
    )
