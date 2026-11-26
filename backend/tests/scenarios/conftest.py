"""Сессия на живом Postgres в транзакции, которая откатывается после теста."""

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules import all_models  # noqa: F401


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


@pytest.fixture
def client(db):
    """HTTP-клиент, у которого все запросы идут в ту же откатываемую транзакцию."""
    from fastapi.testclient import TestClient

    from app.core.db import get_session
    from app.main import app

    app.dependency_overrides[get_session] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()
