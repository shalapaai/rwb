"""Заполняет БД демо-городом из routing и отказами на mesh-полках.

Запускается через make seed после применения миграции N1.
"""

import random
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.core.db import Base, SessionLocal
from app.modules import all_models  # noqa: F401
from app.modules.catalog.models import Category, Sale
from app.modules.inventory.models import MAX_MESH_UNIT_L, Product, Seller, Unit
from app.modules.inventory.service import sim_now
from app.modules.network.models import Node, VanRoute, VanStop
from app.modules.ops.models import SimClock
from app.modules.routing.demo import demo_city
from app.modules.routing.models import City, NodeKind, Sku

SELLER_ID = "S-01"
MESH_SHARE_PCT = 25
DEMO_BOXES = tuple(Sku(id=f"SKU-BOX-{litres}L", volume_l=litres) for litres in (3, 5, 8, 10, 50))

MARKING_REASON = "Маркировка «Честный знак»"
CATEGORIES = [
    ("kitchen", "Кухня", None, False),
    ("books", "Книги", None, False),
    ("office", "Канцелярия", None, False),
    ("electronics", "Электроника", None, False),
    ("sports", "Спорт", None, False),
    ("toys", "Игрушки", None, False),
    ("tools", "Инструменты и хранение", None, False),
    ("furniture", "Мебель и крупное", f"Объём больше {MAX_MESH_UNIT_L} л", False),
    ("shoes", "Обувь", MARKING_REASON, True),
    ("perfume", "Парфюмерия", MARKING_REASON, True),
]
DEMO_CATEGORIES = {
    "SKU-001": "kitchen",
    "SKU-002": "books",
    "SKU-003": "sports",
    "SKU-005": "toys",
}
# Синтетические карточки: номер SKU, категория, название, объём упаковки в л, цена в коп.
EXTRA_PRODUCTS = [
    (4, "office", "Блокнот в клетку", 0.4, 25_000),
    (6, "electronics", "Кабель зарядки", 0.2, 39_000),
    (7, "tools", "Рулетка", 0.4, 45_000),
    (8, "furniture", "Табурет", 24.0, 180_000),
    (9, "shoes", "Кеды", 4.5, 320_000),
    (10, "perfume", "Духи цветочные", 0.4, 210_000),
    (11, "kitchen", "Венчик", 0.8, 35_000),
    (12, "books", "Атлас города", 1.2, 85_000),
    (13, "sports", "Эспандер", 0.6, 55_000),
    (14, "office", "Набор карандашей", 0.2, 18_000),
    (15, "toys", "Кубики", 2.4, 78_000),
    (16, "electronics", "Мышь проводная", 0.5, 89_000),
    (17, "tools", "Отвёртка", 0.3, 29_000),
    (18, "furniture", "Полка настенная", 12.0, 145_000),
    (19, "shoes", "Тапочки", 2.5, 95_000),
    (20, "perfume", "Туалетная вода цитрусовая", 0.5, 160_000),
    (21, "kitchen", "Контейнер для обеда", 1.4, 69_000),
    (22, "books", "Путеводитель", 0.7, 59_000),
    (23, "sports", "Скакалка", 0.8, 49_000),
    (24, "office", "Пенал", 0.5, 32_000),
    (25, "toys", "Пазл", 1.7, 63_000),
    (26, "electronics", "Наушники проводные", 0.3, 119_000),
    (27, "tools", "Набор бит", 0.4, 64_000),
    (28, "furniture", "Столик складной", 36.0, 390_000),
    (29, "shoes", "Балетки", 3.0, 230_000),
    (30, "perfume", "Одеколон", 0.4, 98_000),
    (31, "kitchen", "Тёрка", 1.1, 48_000),
    (32, "books", "Сборник рассказов", 0.8, 72_000),
    (33, "sports", "Мяч для тенниса", 0.4, 28_000),
    (34, "office", "Папка на молнии", 0.6, 21_000),
    (35, "toys", "Машинка", 0.7, 54_000),
    (36, "electronics", "USB-разветвитель", 0.3, 79_000),
    (37, "tools", "Плоскогубцы", 0.5, 67_000),
    (38, "furniture", "Тумба", 42.0, 480_000),
    (39, "shoes", "Сандалии", 3.4, 189_000),
    (40, "perfume", "Духи древесные", 0.3, 245_000),
    (41, "kitchen", "Мерный стакан", 0.9, 31_000),
    (42, "books", "Справочник растений", 1.0, 81_000),
    (43, "sports", "Лента для растяжки", 0.4, 42_000),
    (44, "office", "Точилка", 0.1, 12_000),
    (45, "toys", "Домино", 0.8, 56_000),
]


def _insert_missing(
    session: Session, model: type[Base], keys: tuple[str, ...], rows: list[dict[str, object]]
) -> None:
    if rows:
        session.execute(insert(model).on_conflict_do_nothing(index_elements=keys), rows)


def _seed_sales(session: Session, skus: list[str], pvz_ids: list[str], end_day: date) -> None:
    """Записывает дневные итоги по городу. Отсутствие строки означает нулевые продажи.

    Первые 15 SKU — хиты, последние 10 — редкие, остальные — обычные.
    """
    rng = random.Random(42)
    hits, regular, rare = skus[:15], skus[15:-10], skus[-10:]
    rows = []
    for month in range(2):
        first_day = end_day - timedelta(days=59 - 30 * month)
        for offset in range(30):
            # Каждый ПВЗ продаёт хотя бы один хит в день; набор хитов у ПВЗ меняется.
            rng.shuffle(pvz_ids)
            for index, sku in enumerate(hits):
                rows.append(
                    {
                        "sku": sku,
                        "pvz_id": pvz_ids[index % len(pvz_ids)],
                        "day": first_day + timedelta(days=offset),
                        "qty": rng.randint(1, 3),
                    }
                )
        for group, lower, upper in ((regular, 3, 29), (rare, 0, 2)):
            for sku in group:
                for offset in rng.sample(range(30), rng.randint(lower, upper)):
                    rows.append(
                        {
                            "sku": sku,
                            "pvz_id": rng.choice(pvz_ids),
                            "day": first_day + timedelta(days=offset),
                            "qty": 1,
                        }
                    )
    _insert_missing(session, Sale, ("sku", "pvz_id", "day"), rows)


def _seed_catalog(session: Session, city: City) -> None:
    _insert_missing(
        session,
        Category,
        ("id",),
        [
            {
                "id": key,
                "name": name,
                "mesh_allowed": reason is None,
                "ban_reason": reason,
                "marked": marked,
            }
            for key, name, reason, marked in CATEGORIES
        ],
    )
    _insert_missing(
        session,
        Seller,
        ("id",),
        [
            {"id": SELLER_ID, "name": "Демо-продавец"},
            *[{"id": f"S-{n:02}", "name": f"Демо-продавец {n}"} for n in range(2, 9)],
        ],
    )
    products = [
        {
            "sku": sku.id,
            "name": sku.id,
            "volume_l": sku.volume_l,
            "price_kop": sku.price_kop,
            "seller_id": SELLER_ID,
            "category_id": DEMO_CATEGORIES[sku.id],
            "mesh_allowed": True,
        }
        for sku in city.skus
    ]
    for box in DEMO_BOXES:
        fits = box.volume_l <= MAX_MESH_UNIT_L
        products.append(
            {
                "sku": box.id,
                "name": "Коробка для хранения",
                "volume_l": box.volume_l,
                "price_kop": box.price_kop,
                "seller_id": SELLER_ID,
                "category_id": "tools" if fits else "furniture",
                "mesh_allowed": fits,
            }
        )
    allowed = {key for key, _, reason, _ in CATEGORIES if reason is None}
    for number, category, name, volume, price in EXTRA_PRODUCTS:
        products.append(
            {
                "sku": f"SKU-{number:03}",
                "name": name,
                "volume_l": volume,
                "price_kop": price,
                "seller_id": f"S-{(number - 1) % 8 + 1:02}",
                "category_id": category,
                "mesh_allowed": category in allowed,
            }
        )
    # При заполнении категории учитываем запрет как карточки, так и самой категории.
    stmt = insert(Product.__table__)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["sku"],
            set_={
                "category_id": stmt.excluded.category_id,
                "mesh_allowed": Product.mesh_allowed & stmt.excluded.mesh_allowed,
            },
            where=Product.category_id.is_(None),
        ),
        products,
    )

    # Дата первого демо-рейса сохраняется в БД, повтор завтра воспроизведёт те же дни.
    end_day = session.scalars(
        select(VanRoute.route_date).where(VanRoute.id == city.routes[0].id)
    ).one()
    pvz_ids = sorted(node.id for node in city.nodes if node.kind == NodeKind.PVZ)
    # Коробки до 10 л — хиты: на них в ПВЗ показывают заполнение полки и очередь, а правило
    # MX2 пускает на полку только товары со спросом не ниже порога модели.
    boxes = [p["sku"] for p in products if p["sku"].startswith("SKU-BOX-") and p["mesh_allowed"]]
    rest = sorted(p["sku"] for p in products if p["sku"] not in boxes)
    _seed_sales(session, boxes + rest, pvz_ids, end_day)


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

    _seed_catalog(session, city)

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
