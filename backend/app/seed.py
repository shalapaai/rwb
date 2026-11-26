"""Заполняет БД демо-городом из routing и отказами на mesh-полках.

Запускается через make seed после применения миграции N1.
"""

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.core.db import Base, SessionLocal
from app.modules import all_models  # noqa: F401
from app.modules.inventory.models import Product, Seller, Unit
from app.modules.inventory.service import sim_now
from app.modules.network.models import Node, VanRoute, VanStop
from app.modules.ops.models import SimClock
from app.modules.routing.demo import demo_city
from app.modules.routing.models import NodeKind, Sku

SELLER_ID = "S-01"
MESH_SHARE_PCT = 25
DEMO_BOXES = tuple(Sku(id=f"SKU-BOX-{litres}L", volume_l=litres) for litres in (3, 5, 8, 10, 50))


def _insert_missing(
    session: Session, model: type[Base], keys: tuple[str, ...], rows: list[dict[str, object]]
) -> None:
    if rows:
        session.execute(insert(model).on_conflict_do_nothing(index_elements=keys), rows)


def seed(session: Session) -> None:
    """Добавляет недостающие строки демо-города в транзакции вызывающего кода."""
    city = demo_city()
    quantities: Counter[tuple[str, str]] = Counter()
    for stock in city.stock:
        quantities[stock.node_id, stock.sku_id] += stock.qty
    skus = {sku.id: sku for sku in city.skus}
    used_l: dict[str, float] = defaultdict(float)
    for (node_id, sku_id), qty in quantities.items():
        used_l[node_id] += skus[sku_id].volume_l * qty

    _insert_missing(session, SimClock, ("id",), [{"id": 1, "base_at": datetime.now(UTC)}])
    now = sim_now(session)
    nodes = []
    for node in city.nodes:
        # Демо задаёт свободный объём. Добавляем объём отказов, чтобы его сохранить.
        shelf_limit_l = node.shelf_free_l + used_l[node.id] if node.kind == NodeKind.PVZ else 0
        nodes.append(
            {
                "id": node.id,
                "kind": node.kind.value,
                "name": node.id,
                "x_km": node.x_km,
                "y_km": node.y_km,
                "storage_total_l": shelf_limit_l * 100 / MESH_SHARE_PCT,
                "mesh_share_pct": MESH_SHARE_PCT,
                "is_open": node.is_open,
                "ops_limit": city.params.pvz_daily_ops_limit,
            }
        )
    _insert_missing(session, Node, ("id",), nodes)

    _insert_missing(
        session,
        VanRoute,
        ("id",),
        [
            {
                "id": route.id,
                "depot_id": route.depot_id,
                "capacity": route.capacity,
                "route_date": now.date(),
            }
            for route in city.routes
        ],
    )
    _insert_missing(
        session,
        VanStop,
        ("route_id", "seq"),
        [
            {"route_id": route.id, "seq": seq, **stop.model_dump()}
            for route in city.routes
            for seq, stop in enumerate(route.stops)
        ],
    )

    _insert_missing(session, Seller, ("id",), [{"id": SELLER_ID, "name": "Демо-продавец"}])
    _insert_missing(
        session,
        Product,
        ("sku",),
        [
            {
                "sku": sku.id,
                "name": "Коробка для хранения" if sku in DEMO_BOXES else sku.id,
                "volume_l": sku.volume_l,
                "price_kop": sku.price_kop,
                "seller_id": SELLER_ID,
            }
            for sku in (*city.skus, *DEMO_BOXES)
        ],
    )

    # БД выдаёт числовой id, а стабильный штрихкод находит ту же единицу при повторе.
    _insert_missing(
        session,
        Unit,
        ("barcode",),
        [
            {
                "barcode": str(uuid5(NAMESPACE_URL, f"mesh-demo/{node_id}/{sku}/{index}")),
                "sku": sku,
                "node_id": node_id,
                "status": "on_shelf",
                "grade": "A",
                "shelved_at": now - timedelta(days=index % 11),
            }
            for (node_id, sku), qty in quantities.items()
            for index in range(qty)
        ],
    )


def main() -> None:
    try:
        with SessionLocal.begin() as session:
            seed(session)
    except ProgrammingError as exc:
        if getattr(exc.orig, "sqlstate", None) == "42P01":
            raise SystemExit(
                "Таблицы N1 отсутствуют. Примените миграцию перед запуском сида."
            ) from exc
        raise


if __name__ == "__main__":
    main()
