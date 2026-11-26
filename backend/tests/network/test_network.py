"""Проверки наполнения демо-сети и её чтения через API.

Схему берём из моделей N1, чтобы тесты учитывали реальные ключи и поля.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.auth import Role
from app.core.db import Base, get_session
from app.main import app
from app.modules.inventory.models import Product, Seller, Unit
from app.modules.network.models import Node, VanRoute, VanStop
from app.modules.routing.demo import demo_city
from app.seed import seed


@pytest.fixture
def sessions() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    tables = [model.__table__ for model in (Node, VanRoute, VanStop, Seller, Product, Unit)]
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys = ON")
            Base.metadata.create_all(connection, tables=tables)
        yield sessionmaker(engine)
    finally:
        engine.dispose()


def test_seed_twice_keeps_counts_and_existing_units(sessions: sessionmaker[Session]) -> None:
    city = demo_city()
    expected_counts = {
        Node: len(city.nodes),
        VanRoute: len(city.routes),
        VanStop: sum(len(route.stops) for route in city.routes),
        Seller: 1,
        Product: len(city.skus),
        Unit: sum(item.qty for item in city.stock),
    }
    unit_snapshot = select(
        Unit.id, Unit.barcode, Unit.status, Unit.grade, Unit.shelved_at, Unit.version
    ).order_by(Unit.id)
    before = datetime.now(UTC)
    with sessions.begin() as session:
        seed(session)
        after = datetime.now(UTC)
        for model, count in expected_counts.items():
            assert session.scalar(select(func.count()).select_from(model)) == count
        assert session.scalar(select(Seller.id)) == "S-01"
        products = session.execute(select(Product.sku, Product.price_kop, Product.seller_id))
        assert {tuple(product) for product in products} == {
            (sku.id, sku.price_kop, "S-01") for sku in city.skus
        }
        for route_date in session.scalars(select(VanRoute.route_date)):
            assert before.date() <= route_date <= after.date()
        units = session.execute(unit_snapshot).all()
        for unit in units:
            assert (unit.status, unit.grade, unit.version) == ("on_shelf", "A", 1)
            assert unit.shelved_at is not None
            assert before - timedelta(days=10) <= unit.shelved_at.replace(tzinfo=UTC) <= after

    with sessions.begin() as session:
        seed(session)
        for model, count in expected_counts.items():
            assert session.scalar(select(func.count()).select_from(model)) == count
        assert session.execute(unit_snapshot).all() == units


@pytest.mark.parametrize("role", list(Role))
def test_network_reads_routes_and_current_shelf_load(
    sessions: sessionmaker[Session], role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    with sessions.begin() as session:
        seed(session)
        # Состояние после изменения настроек, резервирования и отправки единиц.
        node = session.get(Node, "ПВЗ-A")
        assert node is not None
        node.mesh_share_pct = 50
        for unit in session.scalars(select(Unit).where(Unit.node_id == node.id)):
            unit.status = "issued" if unit.sku == "SKU-003" else "reserved"

    with sessions.begin() as session:
        seed(session)

    def get_test_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_session, get_test_session)
    with TestClient(app) as client:
        response = client.get("/network", headers={"X-Role": role.value})
    assert response.status_code == 200
    payload = response.json()
    city = demo_city()
    nodes = {node["id"]: node for node in payload["nodes"]}
    assert set(nodes) == {node.id for node in city.nodes}
    assert nodes["ПВЗ-A"]["shelf_used_l"] == 40
    assert nodes["ПВЗ-A"]["shelf_limit_l"] == 1100
    assert nodes["ПВЗ-B"]["shelf_used_l"] == 0
    assert nodes["ПВЗ-B"]["shelf_limit_l"] == 500
    assert nodes["HUB"]["shelf_limit_l"] == 0
    assert payload["routes"] == [route.model_dump(mode="json") for route in city.routes]
