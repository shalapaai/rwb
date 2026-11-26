"""Проверки каталога NK1 на чистой базе с внешними ключами.

Спрос считаем по всем ПВЗ города за каждый из двух 30-дневных периодов.
Повторяем сид после удаления части истории и после смены календарной даты.
"""

from collections import Counter, defaultdict
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import seed as demo_seed
from app.core.db import Base
from app.modules.catalog.models import Category, Sale
from app.modules.inventory.models import Product, Seller, Unit
from app.modules.network.models import Node, VanRoute, VanStop
from app.modules.ops.models import SimClock
from app.modules.routing.demo import demo_city
from app.modules.routing.models import NodeKind


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    models = (Category, Sale, Node, VanRoute, VanStop, Seller, Product, Unit, SimClock)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys = ON")
        Base.metadata.create_all(conn, tables=[model.__table__ for model in models])
        with Session(conn) as session:
            yield session
    engine.dispose()


def test_catalog_and_demo_products(db: Session) -> None:
    demo_seed.seed(db)
    categories = {c.id: c for c in db.scalars(select(Category))}
    assert len(categories) == 10
    banned = {c.name: (c.marked, c.ban_reason) for c in categories.values() if not c.mesh_allowed}
    assert banned == {
        "Мебель и крупное": (False, "Объём больше 10 л"),
        "Обувь": (True, "Маркировка «Честный знак»"),
        "Парфюмерия": (True, "Маркировка «Честный знак»"),
    }
    products = {p.sku: p for p in db.scalars(select(Product))}
    assert set(products) == (
        {f"SKU-{n:03}" for n in range(1, 46)} | {f"SKU-BOX-{size}L" for size in (3, 5, 8, 10, 50)}
    )
    assert {p.category_id for p in products.values()} == set(categories)
    sellers = {f"S-{n:02}" for n in range(1, 9)}
    assert set(db.scalars(select(Seller.id))) == sellers
    assert {p.seller_id for p in products.values()} == sellers
    for product in products.values():
        category = categories[product.category_id]
        assert product.mesh_allowed == category.mesh_allowed
        assert isinstance(product.price_kop, int) and product.price_kop > 0
        assert (product.volume_l > 10) == (category.name == "Мебель и крупное")
    for sku in demo_city().skus:
        product = products[sku.id]
        assert (product.price_kop, product.volume_l, product.seller_id) == (
            sku.price_kop,
            sku.volume_l,
            "S-01",
        )


def test_sales_cover_city_and_distinguish_demand(db: Session) -> None:
    demo_seed.seed(db)
    pvz_ids = {node.id for node in demo_city().nodes if node.kind == NodeKind.PVZ}
    assert len(pvz_ids) == 11
    days: dict[date, set[str]] = defaultdict(set)
    end_day = db.get(VanRoute, "G1").route_date
    totals = [Counter(), Counter()]
    for sku, pvz, day, qty in db.execute(select(Sale.sku, Sale.pvz_id, Sale.day, Sale.qty)):
        days[day].add(pvz)
        assert qty > 0
        totals[(end_day - day).days // 30][sku] += qty
    assert set(days) == {end_day - timedelta(days=n) for n in range(60)}
    assert all(points == pvz_ids for points in days.values())
    skus = list(db.scalars(select(Product.sku)))
    for month in totals:
        assert sum(month[sku] <= 2 for sku in skus) == 10
        assert sum(month[sku] >= 30 for sku in skus) == 15


def test_seed_restores_same_history_and_preserves_existing_data(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db.add(Seller(id="S-01", name="Прежний продавец"))
    db.flush()
    db.add(
        Product(
            sku="SKU-001",
            seller_id="S-01",
            name="Прежняя карточка",
            volume_l=2.0,
            price_kop=123_456,
        )
    )
    db.flush()
    demo_seed.seed(db)
    product = db.get(Product, "SKU-001")
    assert product.category_id is not None
    assert (product.name, product.price_kop) == ("Прежняя карточка", 123_456)
    assert db.get(Seller, "S-01").name == "Прежний продавец"

    query = select(Sale.sku, Sale.pvz_id, Sale.day, Sale.qty).order_by(
        Sale.sku, Sale.pvz_id, Sale.day
    )
    original = db.execute(query).all()
    db.execute(delete(Sale).where(Sale.sku != "SKU-001"))
    db.get(Sale, tuple(original[0][:3])).qty += 1
    db.flush()

    class Later(datetime):
        @classmethod
        def now(cls, tz=UTC):
            return datetime.now(tz) + timedelta(days=3)

    monkeypatch.setattr(demo_seed, "datetime", Later)
    demo_seed.seed(db)
    first, *rest = db.execute(query).all()
    assert tuple(first) == (*original[0][:3], original[0].qty + 1)
    assert rest == original[1:]


@pytest.mark.parametrize(
    "sku,pvz,qty",
    [
        ("SKU-999", "ПВЗ-A", 1),
        ("SKU-001", "ПВЗ-?", 1),
        ("SKU-001", "ПВЗ-A", -1),
    ],
)
def test_sale_constraints(db: Session, sku: str, pvz: str, qty: int) -> None:
    demo_seed.seed(db)
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(Sale(sku=sku, pvz_id=pvz, day=date(2000, 1, 1), qty=qty))
        db.flush()
