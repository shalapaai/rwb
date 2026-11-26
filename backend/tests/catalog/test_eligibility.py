"""Допуск отказа на mesh-полку (MX1): запреты, спрос против порога модели, эндпоинт."""

from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_session
from app.main import app
from app.modules.catalog import service
from app.modules.catalog.models import Category, Sale
from app.modules.catalog.rules import MAX_VOLUME_L, ProductFacts, decide
from app.modules.inventory.models import Product, Seller
from app.modules.network.models import Node

OK = ProductFacts(
    volume_l=2.0,
    product_allowed=True,
    category_allowed=True,
    category_ban_reason=None,
    marked=False,
)
LOW, HIGH, THR = Decimal("0.01"), Decimal("1"), Decimal("0.026")


def facts(**kw):
    return ProductFacts(**{**OK.__dict__, **kw})


# ---------------------------------------------------------------- правило без БД


def test_popular_allowed_product_goes_to_mesh():
    ok, reason = decide(OK, HIGH, THR)
    assert ok and "не ниже порога" in reason


def test_unpopular_goes_to_hub_with_numbers():
    ok, reason = decide(OK, LOW, THR)
    assert not ok and "0,01/сут ниже порога 0,026/сут" in reason


@pytest.mark.parametrize(
    ("kw", "text"),
    [
        ({"category_allowed": False, "category_ban_reason": "крупное"}, "категория запрещена"),
        ({"marked": True}, "маркированный"),
        ({"volume_l": MAX_VOLUME_L + 0.5}, "больше 10 л"),
        ({"product_allowed": False}, "не допущен"),
    ],
)
def test_bans_win_even_for_hits(kw, text):
    ok, reason = decide(facts(**kw), HIGH, THR)  # хит продаж, но запрещён
    assert not ok and text in reason


def test_exactly_at_threshold_is_allowed():
    assert decide(OK, THR, THR)[0]


def test_threshold_comes_from_model_boundary():
    t = service.threshold_per_day()
    assert Decimal("0") < t < Decimal("0.1")  # ≈ 0,026 в сутки при базовых допущениях


# ---------------------------------------------------------------- данные (таблицы NK1)


@pytest.fixture
def db():
    engine = create_engine(get_settings().database_url)
    try:
        migrated = inspect(engine).has_table("sales")
    except OperationalError:
        pytest.skip("Postgres недоступен: docker compose up db")
    if not migrated:
        pytest.skip("Миграция каталога не применена: alembic upgrade head")
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    trans.rollback()
    conn.close()
    engine.dispose()


def test_demand_and_eligibility_from_sales(db):
    today = service._today(db)
    db.add(Seller(id="T-S", name="Тест"))
    db.add(
        Node(
            id="T-P",
            kind="pvz",
            name="",
            x_km=0,
            y_km=0,
            storage_total_l=100,
            mesh_share_pct=25,
            is_open=True,
            ops_limit=60,
        )
    )
    db.add_all(
        [
            Category(id="T-OK", name="Тест-ок", mesh_allowed=True, ban_reason=None, marked=False),
            Category(id="T-SH", name="Тест-обувь", mesh_allowed=True, ban_reason=None, marked=True),
        ]
    )
    db.flush()
    base = {"seller_id": "T-S", "volume_l": 1.0, "price_kop": 100, "mesh_allowed": True}
    db.add_all(
        [
            Product(**base, sku="T-HIT", name="хит", category_id="T-OK"),
            Product(**base, sku="T-DUD", name="без продаж", category_id="T-OK"),
            Product(**base, sku="T-SHOE", name="кроссовки", category_id="T-SH"),
        ]
    )
    db.flush()
    days = [today - timedelta(days=d) for d in range(30)]
    db.add_all(Sale(sku="T-HIT", pvz_id="T-P", day=d, qty=1) for d in days)
    db.add_all(Sale(sku="T-SHOE", pvz_id="T-P", day=d, qty=3) for d in days)
    db.add(Sale(sku="T-HIT", pvz_id="T-P", day=today - timedelta(days=45), qty=99))
    db.flush()

    stats = service.demand(db)
    assert stats["T-HIT"] == (30, Decimal(1))  # продажа 45 дней назад в окно не попала
    assert service.eligibility(db, "T-HIT")[0] is True
    ok, reason = service.eligibility(db, "T-DUD")
    assert not ok and "ниже порога" in reason
    ok, reason = service.eligibility(db, "T-SHOE")
    assert not ok and "маркированный" in reason  # продаётся лучше всех, но маркирован

    app.dependency_overrides[get_session] = lambda: db
    try:
        out = (
            TestClient(app).get("/catalog/products?seller=T-S", headers={"X-Role": "logist"}).json()
        )
    finally:
        app.dependency_overrides.clear()
    assert [r["sku"] for r in out] == ["T-SHOE", "T-HIT", "T-DUD"]  # по спросу
    assert out[1]["sales_30d"] == 30 and out[1]["mesh_eligible"] is True
