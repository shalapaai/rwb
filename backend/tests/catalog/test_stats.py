"""Проверка HTTP-контракта статистики каталога на небольшом наборе продаж.

Продажи разных продавцов, старые заказы и будущие даты проверяют границы сводок.
Товар без продаж и запрещённая категория остаются видны в ответе.
"""

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.main import app
from app.modules.catalog.models import Category, Sale
from app.modules.inventory.models import Product, Seller
from app.modules.inventory.service import sim_now
from app.modules.network.models import Node
from app.modules.ops.models import SimClock

SELLER = {"X-Role": "seller"}
LOGIST = {"X-Role": "logist"}


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    models = (Category, Sale, Node, Seller, Product, SimClock)
    Base.metadata.create_all(engine, tables=[model.__table__ for model in models])
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def client(db: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setitem(app.dependency_overrides, get_session, lambda: db)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def history(db: Session) -> None:
    db.add_all(
        [
            Category(id="ok", name="Разрешённая", mesh_allowed=True, marked=False),
            Category(
                id="blocked",
                name="Обувь",
                mesh_allowed=False,
                marked=True,
                ban_reason="Маркировка",
            ),
            Category(id="empty", name="Без продаж", mesh_allowed=True, marked=False),
            Seller(id="mine", name="Магазин"),
            Seller(id="other", name="Другой"),
            Node(id="A", x_km=0, y_km=0),
            Node(id="B", x_km=1, y_km=0),
            SimClock(id=1, offset_days=2),
        ]
    )
    db.flush()
    for sku, seller, category in (
        ("hit", "mine", "ok"),
        ("quiet", "mine", "ok"),
        ("shoes", "mine", "blocked"),
        ("another", "other", "ok"),
        ("legacy", "other", None),
    ):
        db.add(
            Product(
                sku=sku,
                name=sku,
                seller_id=seller,
                category_id=category,
                volume_l=1,
                price_kop=400,
            )
        )
    db.flush()
    today = sim_now(db).date()
    for sku, pvz, offset, qty in (
        ("hit", "A", 0, 30),
        ("hit", "B", -29, 10),
        ("shoes", "A", 0, 20),
        ("another", "B", 0, 35),
        ("legacy", "B", 0, 5),
        ("hit", "A", -30, 80),
        ("hit", "B", 1, 80),
    ):
        db.add(Sale(sku=sku, pvz_id=pvz, day=today + timedelta(days=offset), qty=qty))
    db.flush()


@pytest.mark.usefixtures("history")
def test_seller_stats_uses_catalog_rules_and_counts_only_its_sales(client: TestClient) -> None:
    response = client.get("/sellers/mine/stats", headers=SELLER)
    assert response.status_code == 200
    data = response.json()
    assert data["seller_id"] == "mine"
    assert data["name"] == "Магазин"
    assert (data["sales_30d"], data["eligible"], data["total"]) == (60, 1, 3)
    assert data["by_pvz"] == [{"pvz_id": "A", "sales_30d": 50}, {"pvz_id": "B", "sales_30d": 10}]
    products = data["products"]
    assert products == client.get("/catalog/products?seller=mine", headers=SELLER).json()
    assert [(p["sku"], p["sales_30d"], p["mesh_eligible"]) for p in products] == [
        ("hit", 40, True),
        ("shoes", 20, False),
        ("quiet", 0, False),
    ]
    assert Decimal(products[0]["demand_per_day"]) == Decimal(40) / 30
    assert products[0]["price_kop"] == 400
    assert "Маркировка" in products[1]["reason"]


@pytest.mark.usefixtures("history")
def test_category_shares_include_city_sales_without_category(client: TestClient) -> None:
    response = client.get("/catalog/categories", headers=LOGIST)
    assert response.status_code == 200
    rows = response.json()
    assert [(row["id"], row["sales_30d"], row["share_pct"]) for row in rows] == [
        ("ok", 75, 75),
        ("blocked", 20, 20),
        ("empty", 0, 0),
    ]
    assert rows[1]["mesh_allowed"] is False
    assert rows[1]["ban_reason"] == "Маркировка"


def test_empty_history_and_missing_seller(client: TestClient, db: Session) -> None:
    db.add(Category(id="empty", name="Без продаж", mesh_allowed=True, marked=False))
    db.add(Seller(id="empty", name="Новый продавец"))
    db.flush()
    assert client.get("/catalog/categories", headers=LOGIST).json()[0]["share_pct"] == 0
    assert client.get("/sellers/empty/stats", headers=SELLER).json() == {
        "seller_id": "empty",
        "name": "Новый продавец",
        "sales_30d": 0,
        "eligible": 0,
        "total": 0,
        "products": [],
        "by_pvz": [],
    }
    assert client.get("/sellers/missing/stats", headers=SELLER).status_code == 404
    assert client.get("/sellers/empty/stats", headers={"X-Role": "pvz_operator"}).status_code == 403
