"""Двойная запись (N3): каждая проводка в сумме 0, повтор не дублирует, только int."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.core.db import get_session
from app.main import app
from app.modules.ledger.models import LedgerEntry, LedgerTxn
from app.modules.ledger.service import LedgerError, post, unbalanced_txn_ids

SALE = [("ext:buyer", -150000), ("mp", 150000)]  # покупатель оплатил 1 500,00 ₽


def test_every_txn_balanced(db):
    """Пример проводок заказа из docs/03 — сумма каждой проводки 0."""
    post(db, "mesh", "sale", None, SALE)
    post(db, "mesh", "sale", None, [("mp", -114600), ("seller:S-01", 114600)])
    post(db, "mesh", "tariff", None, [("mp", -1700), ("pvz:ПВЗ-A", 1700)])
    post(db, "mesh", "cost", None, [("pvz:ПВЗ-A", -938), ("ext:labour", 938)])
    bad = db.scalars(
        text("select txn_id from ledger_entry group by txn_id having sum(amount_kop) <> 0")
    ).all()
    assert bad == []
    assert unbalanced_txn_ids(db) == []


def test_unbalanced_rejected(db):
    before = db.scalar(select(func.count()).select_from(LedgerTxn))
    with pytest.raises(LedgerError, match="не сбалансирована"):
        post(db, "mesh", "sale", None, [("ext:buyer", -150000), ("mp", 149999)])
    assert db.scalar(select(func.count()).select_from(LedgerTxn)) == before  # ничего не записано


def test_rejects_float(db):
    """Инвариант 1: деньги — только целые копейки."""
    with pytest.raises(LedgerError, match="целые копейки"):
        post(db, "mesh", "sale", None, [("ext:buyer", -1500.0), ("mp", 1500.0)])
    with pytest.raises(LedgerError, match="целые копейки"):
        post(db, "mesh", "sale", None, [("ext:buyer", -1), ("mp", True)])


def test_idempotent_post(db):
    """Повтор с тем же ключом возвращает ту же проводку и не создаёт вторую."""
    first = post(db, "mesh", "sale", None, SALE, key="order-1:paid")
    again = post(db, "mesh", "sale", None, list(reversed(SALE)), key="order-1:paid")
    assert again.id == first.id
    n = db.scalar(
        select(func.count())
        .select_from(LedgerTxn)
        .where(LedgerTxn.idempotency_key == "order-1:paid")
    )
    assert n == 1
    assert db.scalar(select(func.count()).where(LedgerEntry.txn_id == first.id)) == 2


def test_same_key_other_content_is_error(db):
    post(db, "mesh", "sale", None, SALE, key="k")
    with pytest.raises(LedgerError, match="уже использован"):
        post(db, "mesh", "sale", None, [("ext:buyer", -100), ("mp", 100)], key="k")


@pytest.mark.parametrize(
    ("scheme", "kind", "legs", "msg"),
    [
        ("other", "sale", SALE, "схема"),
        ("mesh", "gift", SALE, "вид"),
        ("mesh", "sale", [("mp", 0), ("ext:buyer", 0)], "нулевая"),
        ("mesh", "sale", [("mp", 100)], "минимум две"),
        ("mesh", "sale", [("bank", -100), ("mp", 100)], "неизвестный счёт"),
        ("mesh", "sale", [("ext:moon", -100), ("mp", 100)], "неизвестный счёт"),
    ],
)
def test_bad_input(db, scheme, kind, legs, msg):
    with pytest.raises(LedgerError, match=msg):
        post(db, scheme, kind, None, legs)


def test_api_lists_txns_and_check(db):
    post(db, "mesh", "sale", None, SALE, key="api-1")
    post(db, "base", "cost", None, [("mp", -1800), ("ext:trunk", 1800)])
    app.dependency_overrides[get_session] = lambda: db
    try:
        c = TestClient(app)
        h = {"X-Role": "seller"}
        rows = c.get("/ledger", headers=h, params={"scheme": "mesh"}).json()
        mine = [r for r in rows if r["idempotency_key"] == "api-1"]
        assert len(mine) == 1 and mine[0]["total_kop"] == 0
        assert {e["account"] for e in mine[0]["entries"]} == {"ext:buyer", "mp"}
        check = c.get("/ledger/check", headers=h).json()
        assert check["ok"] and check["unbalanced_txn_ids"] == []
        assert c.get("/ledger").status_code == 401  # без роли
    finally:
        app.dependency_overrides.clear()
