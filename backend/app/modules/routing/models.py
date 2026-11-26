"""Входные и выходные данные алгоритма логистики.

Единицы: деньги — копейки (int), время — минуты (int), расстояния — км (float).
Все тарифы в Params — синтетические допущения модели v2 (docs/02-business-model.md),
а не тарифы RWB.
"""

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

UNIQUE_SKU_PREFIX = "UNIQUE-"


def is_unique_sku(sku_id: str) -> bool:
    return sku_id.startswith(UNIQUE_SKU_PREFIX)


class Params(BaseModel):
    # --- стоимость обслуживания единицы, коп. ---
    own_kop: int = 2490  # выдача с полки ПВЗ (приёмка, хранение, отбор, выдача): 24,9 ₽
    move_kop: int = 710  # место в попутной газели + отбор и приём: ~7 ₽
    transfer_kop: int = 1300  # пересадка между газелями в ПВЗ: приём 4 ₽ + отбор 9 ₽
    neighbor_discount_kop: int = 300  # скидка покупателю за соседний ПВЗ
    hub_kop: int = 6040  # базовая схема: хаб + магистраль 150 км: 60,4 ₽

    # --- время, ч ---
    own_hours: float = 0.5
    hub_hours: float = 36.0
    hours_until_routes: float = 10.0  # от заказа вечером до выезда газелей утром

    # --- ценность времени покупателя: λ, коп. за час на единицу ---
    lambda_kop_per_hour: int = 50

    # --- газель ---
    van_kop_per_km: int = 2000
    van_kop_per_min: int = 1000  # водитель + машина: 600 ₽/ч
    speed_kmh: float = 36.0
    road_factor: float = 1.3  # дорога длиннее прямой
    season_factor: float = 1.0  # зимой > 1: медленнее и дороже
    stop_service_min: int = 2  # время на новой остановке

    # --- лимиты правки маршрутов ---
    max_added_min_abs: int = 20
    max_added_share: float = 0.10  # не больше +10% к длительности рейса
    max_new_stops: int = 2
    transfer_min_gap: int = 15  # пересадка: вторая газель позже первой минимум на 15 мин

    # --- ПВЗ ---
    neighbor_radius_km: float = 1.5
    pvz_daily_ops_limit: int = 60  # mesh-операций в день на точку (окна без очереди)

    # --- переброска под прогноз ---
    sell_probability: float = 0.6  # вероятность продать переброшенную единицу за горизонт
    cover_days: float = 2.0  # целевой запас: спрос на 2–3 дня…
    safety_z: float = 1.28  # …плюс запас z·σ·√L (формула «газетчика»)

    max_iterations: int = 5  # повторы шага «правка маршрутов»

    @property
    def rebalance_reward_kop(self) -> int:
        """Ожидаемая экономия от единицы, лежащей там, где её купят."""
        return round(self.sell_probability * (self.hub_kop - self.own_kop))


class NodeKind(StrEnum):
    PVZ = "pvz"
    HUB = "hub"


class Node(BaseModel):
    id: str
    kind: NodeKind = NodeKind.PVZ
    x_km: float
    y_km: float
    is_open: bool = True
    shelf_free_l: float = 500.0  # свободное место на mesh-полке


class Stop(BaseModel):
    node_id: str
    eta_min: int  # плановое время прибытия от выезда газели
    service_min: int = 0  # сколько газель стоит здесь (выгрузка коробок из хаба)
    base_drop: int = 0  # сколько посылок из хаба газель оставляет здесь
    added: bool = False  # остановку добавил mesh


class VanRoute(BaseModel):
    id: str
    depot_id: str  # хаб, откуда выезжает газель
    capacity: int = 300  # мест в кузове
    stops: list[Stop]


class Sku(BaseModel):
    id: str
    volume_l: float = 2.0
    price_kop: int = 150000


class StockItem(BaseModel):
    node_id: str
    sku_id: str
    qty: int = Field(ge=0)


class Order(BaseModel):
    id: str
    sku_id: str
    qty: int = Field(default=1, ge=1)
    pvz_id: str  # где покупатель заберёт заказ
    accepts_neighbor: bool = False  # согласен забрать в соседнем ПВЗ
    max_hours: float | None = None  # обещанный срок


class Forecast(BaseModel):
    node_id: str
    sku_id: str
    daily_mean: float
    daily_std: float = 0.0


class City(BaseModel):
    nodes: list[Node]
    routes: list[VanRoute]
    skus: list[Sku]
    stock: list[StockItem]
    orders: list[Order] = Field(default_factory=list)
    forecast: list[Forecast] = Field(default_factory=list)
    params: Params = Field(default_factory=Params)

    @model_validator(mode="after")
    def check_references(self) -> "City":
        """Все ссылки на узлы и артикулы существуют — иначе расчёт упадёт посередине."""
        nodes = {n.id: n for n in self.nodes}
        skus = {s.id for s in self.skus}
        errors = []
        if len(nodes) != len(self.nodes):
            errors.append("повторяются id узлов")
        if len(skus) != len(self.skus):
            errors.append("повторяются id артикулов")
        if len({o.id for o in self.orders}) != len(self.orders):
            errors.append("повторяются id заказов")
        if len({r.id for r in self.routes}) != len(self.routes):
            errors.append("повторяются id маршрутов")
        for r in self.routes:
            if r.depot_id not in nodes:
                errors.append(f"маршрут {r.id}: нет депо {r.depot_id}")
            for s in r.stops:
                if s.node_id not in nodes or nodes[s.node_id].kind != NodeKind.PVZ:
                    errors.append(f"маршрут {r.id}: остановка {s.node_id} не ПВЗ")
            etas = [s.eta_min for s in r.stops]
            if etas != sorted(etas):
                errors.append(f"маршрут {r.id}: время остановок не по возрастанию")
        for kind, items in (("остаток", self.stock), ("прогноз", self.forecast)):
            for it in items:
                if it.node_id not in nodes:
                    errors.append(f"{kind}: нет узла {it.node_id}")
                if it.sku_id not in skus:
                    errors.append(f"{kind}: нет артикула {it.sku_id}")
        for o in self.orders:
            if o.pvz_id not in nodes:
                errors.append(f"заказ {o.id}: нет ПВЗ {o.pvz_id}")
            if o.sku_id not in skus:
                errors.append(f"заказ {o.id}: нет артикула {o.sku_id}")
        if errors:
            raise ValueError("; ".join(errors))
        return self


# ---------------------------------------------------------------- выход


class Variant(StrEnum):
    OWN = "own"  # товар уже в ПВЗ покупателя
    NEIGHBOR = "neighbor"  # покупатель забирает в соседнем ПВЗ
    VAN = "van"  # попутно газелью (возможно с пересадкой)
    HUB = "hub"  # базовая схема


class Leg(BaseModel):
    route_id: str
    from_node: str
    to_node: str
    depart_min: int
    arrive_min: int


class Assignment(BaseModel):
    """Как исполняется (часть) заказа."""

    order_id: str
    sku_id: str
    qty: int
    variant: Variant
    source_node: str
    money_kop: int  # себестоимость на единицу
    hours: float
    legs: list[Leg] = Field(default_factory=list)


class Movement(BaseModel):
    """Переброска под прогноз, без заказа."""

    sku_id: str
    qty: int
    from_node: str
    to_node: str
    legs: list[Leg]
    money_kop: int  # на единицу


class InsertedStop(BaseModel):
    route_id: str
    node_id: str
    position: int
    added_km: float
    added_min: int
    cost_kop: int
    gain_kop: int  # насколько выгоднее стал план с учётом цены крюка


class Candidate(BaseModel):
    variant: Variant
    source_node: str | None = None
    money_kop: int | None = None
    hours: float | None = None
    generalized_kop: int | None = None  # деньги + λ·часы
    gain_vs_hub_kop: int | None = None
    chosen: bool = False
    reason: str  # почему выбран или отброшен


class Plan(BaseModel):
    routes: list[VanRoute]
    assignments: list[Assignment]
    movements: list[Movement]
    inserted_stops: list[InsertedStop]
    unassigned: dict[str, int] = Field(default_factory=dict)  # заказ → штук без маршрута
    total_money_kop: int  # заказы + переброски + крюки
    hub_money_kop: int  # цена заказов, для которых доступна отгрузка из хаба
    objective_kop: int  # то, что минимизирует алгоритм


class SchemeSummary(BaseModel):
    scheme: str
    money_kop: int
    money_per_unit_kop: int
    avg_hours: float
    local_share: float  # доля единиц, исполненных без хаба
    added_km: float
    hub_units: int
    unassigned_units: int = 0
