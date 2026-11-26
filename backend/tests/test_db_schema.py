"""Схема БД (задача N1): все таблицы на месте, ограничения держит сама база.

Тесты с БД идут на живом Postgres (в CI — сервис, локально — `docker compose up db` и
`alembic upgrade head`). Каждый тест работает в транзакции, которая в конце откатывается.
Если БД недоступна или миграции не применены, такие тесты пропускаются с причиной.
"""

import pytest
from sqlalchemy import create_engine, inspect, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.config import get_settings
from app.core.db import Base
from app.modules import all_models  # noqa: F401
from app.modules.inventory.models import Product, Return, Seller, Unit
from app.modules.ledger.models import LedgerEntry, LedgerTxn
from app.modules.network.models import Node
from app.modules.orders.models import Order, Reservation

TABLES = {
    "nodes", "van_routes", "van_stops", "sellers", "products", "units", "orders",
    "reservations", "returns", "ledger_txn", "ledger_entry", "incidents", "notifications",
    "idempotency_keys", "sim_clock", "reduction_dispatches", "shelf_writeoffs",
}  # fmt: skip


def test_metadata_has_all_tables():
    """Список таблиц — как на листе «Таблицы БД» роадмапа."""
    assert set(Base.metadata.tables) == TABLES


def test_money_columns_are_bigint():
    """Инвариант 1: деньги — целые копейки, никакого float."""
    for table, column in [
        (Product, "price_kop"),
        (Order, "cost_kop"),
        (LedgerEntry, "amount_kop"),
    ]:
        assert str(table.__table__.c[column].type) == "BIGINT"


@pytest.fixture
def db():
    engine = create_engine(get_settings().database_url)
    try:
        migrated = inspect(engine).has_table("units")
    except OperationalError:
        pytest.skip("Postgres недоступен: docker compose up db")
    if not migrated:
        pytest.skip("Миграции не применены: alembic upgrade head")
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    trans.rollback()
    conn.close()
    engine.dispose()


def _city(s: Session) -> tuple[Unit, Order]:
    s.add_all([
        Node(id="T-A", kind="pvz", x_km=0, y_km=0, storage_total_l=400),
        Seller(id="T-S1"),
    ])  # fmt: skip
    s.flush()
    s.add(Product(sku="T-SKU", seller_id="T-S1", volume_l=2.0, price_kop=150000))
    s.flush()
    unit = Unit(barcode="T-0001", sku="T-SKU", node_id="T-A", status="on_shelf", grade="A")
    order = Order(sku="T-SKU", pvz_id="T-A")
    s.add_all([unit, order])
    s.flush()
    return unit, order


def test_mesh_share_must_be_10_to_50(db):
    """Решение 4: ПВЗ отдаёт под mesh от 10 до 50% склада."""
    db.add(Node(id="T-X", kind="pvz", x_km=0, y_km=0, mesh_share_pct=60))
    with pytest.raises(IntegrityError):
        db.flush()


def test_default_mesh_limit_is_quarter_of_storage(db):
    _city(db)
    assert db.get(Node, "T-A").mesh_limit_l == 100.0


def test_one_active_reservation_per_unit(db):
    """Инвариант 3: второй активный резерв той же единицы база не пустит."""
    unit, order = _city(db)
    db.add(Reservation(unit_id=unit.id, order_id=order.id, active=False))  # снятый — можно
    db.add(Reservation(unit_id=unit.id, order_id=order.id, active=True))
    db.flush()
    db.add(Reservation(unit_id=unit.id, order_id=order.id, active=True))
    with pytest.raises(IntegrityError):
        db.flush()


def test_stale_version_is_rejected(db):
    """Инвариант 4: если запись успели поменять, наше изменение не пройдёт."""
    unit, _ = _city(db)
    assert unit.version == 1
    # кто-то другой уже поменял единицу: версия в БД ушла вперёд, а наш объект об этом не знает
    db.execute(
        update(Unit).where(Unit.id == unit.id).values(version=2),
        execution_options={"synchronize_session": False},
    )
    unit.status = "reserved"
    with pytest.raises(StaleDataError):
        db.flush()


def test_version_grows_on_update(db):
    unit, _ = _city(db)
    unit.status = "reserved"
    db.flush()
    assert unit.version == 2


def test_return_after_purchase_goes_to_hub(db):
    """Решение N0: возврат после покупки на полку не кладём — только в хаб."""
    unit, order = _city(db)
    db.add(Return(unit_id=unit.id, order_id=order.id, pvz_id="T-A", kind="refusal",
                  grade="A", decision="shelf"))  # fmt: skip
    db.flush()
    db.add(Return(unit_id=unit.id, order_id=order.id, pvz_id="T-A", kind="return",
                  grade="A", decision="shelf"))  # fmt: skip
    with pytest.raises(IntegrityError):
        db.flush()


def test_unknown_status_rejected(db):
    unit, _ = _city(db)
    with pytest.raises(IntegrityError):
        db.execute(update(Unit).where(Unit.id == unit.id).values(status="lost"))


def test_ledger_scheme_and_bigint(db):
    _, order = _city(db)
    txn = LedgerTxn(scheme="mesh", kind="sale", order_id=order.id, idempotency_key="k1")
    db.add(txn)
    db.flush()
    db.add(LedgerEntry(txn_id=txn.id, account="mp", amount_kop=10**12))  # больше int32
    db.flush()
    db.add(LedgerTxn(scheme="other", kind="sale"))
    with pytest.raises(IntegrityError):
        db.flush()
