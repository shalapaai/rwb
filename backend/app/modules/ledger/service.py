"""Двойная запись: `post()` — единственный способ начислить деньги (инвариант 2).

Проводка — набор записей «счёт ± копейки», сумма которых ровно 0: деньги не берутся
из ниоткуда и не исчезают. Тариф — перевод внутри цепочки (mp → pvz:A), реальная
затрата — уход во внешний мир (pvz:A → ext:labour). Записи не правятся и не
удаляются: исправление или возврат — новая проводка с обратными знаками.
Повтор с тем же ключом идемпотентности не создаёт вторую проводку.
Коммит делает вызывающий код: post() только добавляет и сбрасывает (flush) записи.
"""

import re
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.ledger.models import SCHEMES, LedgerEntry, LedgerTxn

KINDS = ("sale", "tariff", "cost", "return", "penalty")
EXTERNAL = ("buyer", "labour", "materials", "van", "hub_ops", "trunk", "shrink")
# mp | seller:<id> | pvz:<id> | ext:<внешний мир из EXTERNAL>
ACCOUNT_RE = re.compile(r"^(mp|seller:[\w-]+|pvz:[\w-]+|ext:(" + "|".join(EXTERNAL) + r"))$")

Leg = tuple[str, int]


class LedgerError(ValueError):
    """Проводку нельзя записать: сумма не 0, не те типы, чужой ключ и т. п."""


def _check(scheme: str, kind: str, legs: Sequence[Leg]) -> list[Leg]:
    if scheme not in SCHEMES:
        raise LedgerError(f"схема {scheme!r}: допустимы {SCHEMES}")
    if kind not in KINDS:
        raise LedgerError(f"вид проводки {kind!r}: допустимы {KINDS}")
    legs = list(legs)
    if len(legs) < 2:
        raise LedgerError("в проводке нужно минимум две записи: откуда и куда")
    for account, amount in legs:
        # bool — тоже int в Python, но деньгами быть не может
        if type(amount) is not int:
            raise LedgerError(f"{account}: сумма {amount!r} — только целые копейки (int)")
        if amount == 0:
            raise LedgerError(f"{account}: нулевая запись не нужна")
        if not ACCOUNT_RE.match(account):
            raise LedgerError(f"неизвестный счёт {account!r}")
    total = sum(amount for _, amount in legs)
    if total != 0:
        raise LedgerError(f"проводка не сбалансирована: сумма {total} коп., должна быть 0")
    return legs


def _same(
    session: Session, txn: LedgerTxn, scheme: str, kind: str, order_id: int | None, legs: list[Leg]
) -> bool:
    entries = sorted((e.account, e.amount_kop) for e in entries_of(session, txn.id))
    return (txn.scheme, txn.kind, txn.order_id, entries) == (scheme, kind, order_id, sorted(legs))


def _existing(
    session: Session, key: str, scheme: str, kind: str, order_id: int | None, legs: list[Leg]
) -> LedgerTxn | None:
    txn = session.scalar(select(LedgerTxn).where(LedgerTxn.idempotency_key == key))
    if txn is not None and not _same(session, txn, scheme, kind, order_id, legs):
        raise LedgerError(f"ключ {key!r} уже использован для другой проводки")
    return txn


def post(
    session: Session,
    scheme: str,
    kind: str,
    order_id: int | None,
    legs: Sequence[Leg],
    key: str | None = None,
) -> LedgerTxn:
    """Записать проводку. legs — [(счёт, копейки)], «+» — начисление, «−» — списание.

    Повтор с тем же key возвращает уже записанную проводку. Тот же key с другим
    содержимым — ошибка: значит, кто-то перепутал ключи.
    """
    legs = _check(scheme, kind, legs)
    if key is not None and (found := _existing(session, key, scheme, kind, order_id, legs)):
        return found
    txn = LedgerTxn(scheme=scheme, kind=kind, order_id=order_id, idempotency_key=key)
    try:
        # savepoint: если параллельный запрос успел вставить тот же ключ, откатим только себя
        with session.begin_nested():
            session.add(txn)
            session.flush()
            session.add_all(LedgerEntry(txn_id=txn.id, account=a, amount_kop=v) for a, v in legs)
            session.flush()
    except IntegrityError:
        # гонка: тот же ключ успел записать параллельный запрос — вернём его проводку;
        # любая другая ошибка целостности (например, нет такого заказа) — наружу
        if key is not None and (found := _existing(session, key, scheme, kind, order_id, legs)):
            return found
        raise
    return txn


def entries_of(session: Session, txn_id: int) -> list[LedgerEntry]:
    return list(
        session.scalars(
            select(LedgerEntry).where(LedgerEntry.txn_id == txn_id).order_by(LedgerEntry.id)
        )
    )


def unbalanced_txn_ids(session: Session) -> list[int]:
    """Проводки, у которых сумма записей не 0. Должно быть пусто всегда."""
    q = (
        select(LedgerEntry.txn_id)
        .group_by(LedgerEntry.txn_id)
        .having(func.sum(LedgerEntry.amount_kop) != 0)
    )
    return list(session.scalars(q))
