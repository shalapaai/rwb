"""Синтетический город для демо и тестов. Все данные выдуманы (п. 8.4 положения).

Координаты — км; хаб в точке (-15, 0), отдельно от группы городских ПВЗ.
Три газели от хаба:
  G1: A → C → B → E      G2: D → F → G      G3: H → I → J
ПВЗ X не стоит ни в одном маршруте — к нему выгоден крюк, только если там ждут
несколько заказов.
"""

from app.modules.routing.models import (
    UNIQUE_SKU_PREFIX,
    City,
    Forecast,
    Node,
    NodeKind,
    Order,
    Params,
    Sku,
    StockItem,
    Stop,
    VanRoute,
)
from app.modules.routing.network import dist_km, travel_min

NODES = [
    Node(id="HUB", kind=NodeKind.HUB, x_km=-15, y_km=0),  # хаб/СЦ за городом
    # После загородного плеча G1 объезжает кварталы: на север, на восток и снова на север.
    Node(id="ПВЗ-A", x_km=6, y_km=2),
    Node(id="ПВЗ-C", x_km=5.1, y_km=5),
    Node(id="ПВЗ-B", x_km=7.4, y_km=4.9),
    Node(id="ПВЗ-E", x_km=9.1, y_km=7),
    Node(id="ПВЗ-X", x_km=10.25, y_km=8.4),
    Node(id="ПВЗ-D", x_km=1.2, y_km=9.5),
    Node(id="ПВЗ-F", x_km=3, y_km=5.6),
    Node(id="ПВЗ-G", x_km=5.8, y_km=8.7),
    Node(id="ПВЗ-H", x_km=11.3, y_km=1.4),
    Node(id="ПВЗ-I", x_km=14.4, y_km=3.4),
    Node(id="ПВЗ-J", x_km=14.1, y_km=8.5),
]


def make_route(
    route_id: str,
    stops: list[tuple[str, int]],
    params: Params,
    depot: str = "HUB",
    capacity: int = 300,
    unload_min: int = 12,
) -> VanRoute:
    """Маршрут по списку (ПВЗ, посылок из хаба); время прибытия считается по расстоянию."""
    nodes = {n.id: n for n in NODES}
    eta, prev, out = 0, depot, []
    for node_id, drop in stops:
        eta += travel_min(dist_km(nodes[prev], nodes[node_id], params), params)
        out.append(Stop(node_id=node_id, eta_min=eta, service_min=unload_min, base_drop=drop))
        eta += unload_min  # обычная остановка: выгрузка коробок из хаба
        prev = node_id
    return VanRoute(id=route_id, depot_id=depot, capacity=capacity, stops=out)


def demo_city(params: Params | None = None, *, include_unique_stock: bool = False) -> City:
    p = params or Params()
    routes = [
        make_route("G1", [("ПВЗ-A", 60), ("ПВЗ-C", 50), ("ПВЗ-B", 70), ("ПВЗ-E", 60)], p),
        make_route("G2", [("ПВЗ-D", 80), ("ПВЗ-F", 70), ("ПВЗ-G", 60)], p),
        make_route("G3", [("ПВЗ-H", 50), ("ПВЗ-I", 60), ("ПВЗ-J", 70)], p),
    ]
    skus = [
        Sku(id="SKU-001", volume_l=2.0, price_kop=150000),  # термокружка
        Sku(id="SKU-002", volume_l=2.0, price_kop=150000),  # есть только в хабе
        Sku(id="SKU-003", volume_l=2.0, price_kop=150000),  # залежалая партия
        Sku(id="SKU-005", volume_l=1.0, price_kop=90000),
    ]
    stock = [
        StockItem(node_id="ПВЗ-A", sku_id="SKU-001", qty=20),
        StockItem(node_id="ПВЗ-A", sku_id="SKU-003", qty=5),
        StockItem(node_id="ПВЗ-H", sku_id="SKU-005", qty=10),
        StockItem(node_id="ПВЗ-J", sku_id="SKU-005", qty=4),
    ]
    # SKU-003 даёт источник для what-if и сохраняет исходный спрос ночного демо.
    stocked_nodes = {item.node_id for item in stock}
    stock.extend(
        StockItem(node_id=node.id, sku_id="SKU-003", qty=3)
        for node in NODES
        if node.kind == NodeKind.PVZ and node.id not in stocked_nodes
    )
    if include_unique_stock:
        # Эти партии нужны на схеме логиста, чтобы проверить перевозку с конкретной полки.
        for node in NODES:
            if node.kind == NodeKind.PVZ:
                sku = Sku(id=f"{UNIQUE_SKU_PREFIX}{node.id}")
                skus.append(sku)
                stock.append(StockItem(node_id=node.id, sku_id=sku.id, qty=3))
    orders = [
        Order(id="O-1", sku_id="SKU-001", pvz_id="ПВЗ-B"),  # попутно G1: A раньше B
        Order(id="O-2", sku_id="SKU-001", pvz_id="ПВЗ-A"),  # свой ПВЗ
        Order(id="O-3", sku_id="SKU-002", pvz_id="ПВЗ-C"),  # нет в городе → хаб
        Order(id="O-4", sku_id="SKU-001", qty=4, pvz_id="ПВЗ-X"),  # крюк окупается от ~5 шт.
        Order(id="O-5", sku_id="SKU-001", qty=3, pvz_id="ПВЗ-X"),
        Order(id="O-6", sku_id="SKU-001", pvz_id="ПВЗ-D"),  # A и D на разных газелях
        Order(id="O-7", sku_id="SKU-005", pvz_id="ПВЗ-J"),  # своя полка J
        Order(id="O-8", sku_id="SKU-005", qty=6, pvz_id="ПВЗ-I"),  # H раньше I на G3
        Order(id="O-9", sku_id="SKU-005", pvz_id="ПВЗ-H"),  # своя полка H
    ]
    forecast = [
        Forecast(node_id="ПВЗ-E", sku_id="SKU-001", daily_mean=2.0, daily_std=1.0),
        Forecast(node_id="ПВЗ-B", sku_id="SKU-001", daily_mean=1.0, daily_std=0.5),
        Forecast(node_id="ПВЗ-A", sku_id="SKU-001", daily_mean=1.0, daily_std=0.5),
    ]
    return City(
        nodes=NODES,
        routes=routes,
        skus=skus,
        stock=stock,
        orders=orders,
        forecast=forecast,
        params=p,
    )
