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
        "коп/заказ",
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
    own_delivery_min: int = assumption(
        30,
        "Готовность со своей полки",
        "мин",
        "Лист «Модель v3»: 0,5 ч с момента заказа.",
        ROADMAP,
    )
    van_delivery_min: int = assumption(
        720,
        "Готовность после попутного рейса",
        "мин",
        "Лист «Модель v3»: около 12 ч с момента заказа, сценарный ориентир.",
        ROADMAP,
    )
    hub_delivery_min: int = assumption(
        2160,
        "Готовность заказа из хаба",
        "мин",
        "Лист «Модель v3»: 36 ч с момента следующего заказа.",
        ROADMAP,
    )
    operation_markup_pct: int = assumption(
        20,
        "Наценка ПВЗ на труд при проверке, переупаковке, отборе и приёмке",
        "%",
        "Синтетическое допущение по примеру проверки в v2: три минуты по 250 ₽/ч "
        "стоили 12,50 ₽ при выплате 15 ₽. Пакет возмещается по себестоимости.",
        DOC,
    )
    commission_pct: int = assumption(
        20,
        "Комиссия маркетплейса с суммы продажи",
        "%",
        "Сценарная ставка из docs/02, одинаковая для продаж по обеим схемам.",
        DOC,
    )
    issue_fee_pct: int = assumption(
        3,
        "Выплата ПВЗ за выдачу в процентах от суммы продажи",
        "%",
        "Сценарное вознаграждение за выдачу из docs/02 для обеих схем.",
        DOC,
    )
    seller_logistics_base_kop: int = assumption(
        6000,
        "Плата продавца за логистику через хаб",
        KOPECK,
        "Базовая ставка продавца за одну единицу из docs/02. "
        "Себестоимость исполнения через хаб учитывается отдельно в hub_order_kop.",
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

    opened_share: Decimal = assumption(
        Decimal("0.75"),
        "Доля B среди пригодных отказов",
        "доля 0–1",
        "Оценка команды 26.11: вскрывают примерно 3 из 4 пригодных отказов. Замерить на пилоте.",
        "Решение команды 26.11",
    )
    loss_share: Decimal = assumption(
        Decimal("0.003"),
        "Ожидаемая доля потерь на mesh-полке",
        "доля 0–1",
        "Оценка v2 для ПВЗ. Резерв на списание при размещении единицы покрывает МП; "
        "базовый хабовый маршрут уже включает свои потери.",
        DOC,
    )
    replacement_kop: int = assumption(
        90000,
        "Стоимость замещения потерянной единицы",
        KOPECK,
        "Товар за 1 500 ₽, условная себестоимость 60% по разделу о недостаче.",
        DOC,
    )
    season_factor: Decimal = assumption(
        Decimal(1),
        "Множитель транспортных затрат попутного плеча",
        "коэффициент",
        "Обычный сезон = 1. Стресс N9 меняет переменную цену места в газели; "
        "стоимость труда ПВЗ и эталон хаба зафиксированы.",
        ROADMAP,
    )
    storage_total_l: int = assumption(
        2000,
        "Склад типового ПВЗ",
        "л",
        "ПВЗ без начального остатка в сиде N2: 500 л свободно при доле mesh 25%.",
        "backend/app/seed.py",
    )
    mesh_share_pct: int = assumption(
        25,
        "Базовая доля склада под mesh",
        "%",
        "Решение команды v3, диапазон 10–50%.",
        ROADMAP,
    )
    utilization: Decimal = assumption(
        Decimal("0.5"),
        "Загрузка выделенной полки",
        "доля 0–1",
        "Целевой сценарий пилота: занята половина выделенного объёма.",
        "docs/05-pilot-and-scaling.md",
    )
    month_days: int = assumption(
        30,
        "Расчётный месяц",
        "сут",
        "Единая база для аренды и литро-дней в docs/02.",
        DOC,
    )
    pvz_demand_per_day: Decimal = assumption(
        Decimal("0.1"),
        "Спрос на единицу среднего ассортимента ПВЗ",
        "ед./сут",
        "Для месячного сценария интервал заказов 10 суток по ориентиру хранения v2. "
        "С учётом вывоза на 14-й день среднее время на полке короче.",
        DOC,
    )

    @property
    def seller_logistics_mesh_kop(self) -> int:
        return self.seller_logistics_base_kop - self.seller_saving_kop

    @property
    def storage_tariff(self) -> int:
        return self.space_kop_l_day + self.shelf_labour_kop_l_day + self.shelf_profit_kop_l_day

    @property
    def repack_cost(self) -> Decimal:
        return self.labour(self.repack_min) + self.pack_kop

    def labour(self, minutes: int | Decimal) -> Decimal:
        return self.wage_kop_hour * Decimal(minutes) / 60

    def operation_fee(self, minutes: int | Decimal, materials_kop: int = 0) -> int:
        labour_with_markup = self.labour(minutes) * (100 + self.operation_markup_pct) / 100
        return kopecks(labour_with_markup + materials_kop)

    @property
    def inspection_fee_kop(self) -> int:
        return self.operation_fee(self.inspection_min)

    @property
    def repack_fee_kop(self) -> int:
        return self.operation_fee(self.repack_min, self.pack_kop)

    @property
    def pick_fee_kop(self) -> int:
        return self.operation_fee(self.pick_min)

    @property
    def receive_fee_kop(self) -> int:
        return self.operation_fee(self.receive_min)

    @property
    def own_cost(self) -> Decimal:
        return self.labour(self.pick_min) + self.it_kop

    @property
    def move_cost(self) -> Decimal:
        return self.labour(self.receive_min) + self.van_space_kop


DEFAULTS = ModelParams()


def catalog(params: ModelParams = DEFAULTS) -> list[Parameter]:
    entries = [
        Parameter(id=f.name, value=getattr(params, f.name), **f.metadata) for f in fields(params)
    ]
    for key, operation in (
        ("inspection_fee_kop", "оценку"),
        ("repack_fee_kop", "переупаковку B"),
        ("pick_fee_kop", "отбор"),
        ("receive_fee_kop", "приём из газели"),
    ):
        basis = "Стоимость труда по нормативу операции × (1 + наценка ПВЗ / 100)."
        if key == "repack_fee_kop":
            basis += " Дополнительно возмещается один пакет по себестоимости."
        entries.append(
            Parameter(
                id=key,
                label=f"Выплата ПВЗ за {operation}",
                value=getattr(params, key),
                unit=KOPECK,
                basis=basis,
                source=DOC,
            )
        )
    return entries + [
        Parameter(
            id="seller_logistics_mesh_kop",
            label="Плата продавца за местное исполнение",
            value=params.seller_logistics_mesh_kop,
            unit=KOPECK,
            basis="Базовая ставка логистики продавца за вычетом скидки за местное исполнение.",
            source=DOC,
        ),
        Parameter(
            id="storage_tariff_kop_l_day",
            label="Полный тариф хранения",
            value=params.storage_tariff,
            unit="коп/(л·сут)",
            basis="Место + труд + прибыль владельца ПВЗ.",
            source=ROADMAP,
        ),
        Parameter(
            id="repack_cost_kop",
            label="Себестоимость переупаковки B",
            value=kopecks(params.repack_cost),
            unit=KOPECK,
            basis="Время переупаковки × стоимость минуты + пакет.",
            source=ROADMAP,
        ),
    ]


def routing_params(params: ModelParams = DEFAULTS) -> Params:
    """Цена исполнения после заказа. Предыдущая оценка и хранение уже оплачены."""
    return Params(
        own_kop=kopecks(params.own_cost),
        move_kop=kopecks(params.move_cost),
        transfer_kop=kopecks(params.labour(params.receive_min + params.pick_min)),
        hub_kop=params.hub_order_kop,
        own_hours=params.own_delivery_min / 60,
        hub_hours=params.hub_delivery_min / 60,
    )
