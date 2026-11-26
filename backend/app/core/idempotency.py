"""Идемпотентность изменяющих запросов (инвариант 5).

Клиент шлёт заголовок `Idempotency-Key`. Первый запрос выполняется, его ответ
сохраняется в `idempotency_keys`; повтор с тем же ключом получает тот же ответ и
ничего не меняет (двойной клик, повтор после обрыва сети). Одновременные запросы
с одним ключом выстраиваются в очередь блокировкой Postgres на время транзакции.
Ключ хранится вместе с областью (scope), чтобы один ключ в разных эндпоинтах не путался.
"""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Header
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.ops.models import IdempotencyKey

IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=100)]


def run_once(
    session: Session, scope: str, key: str, action: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    """Выполнить action один раз на (scope, key); ответ должен быть JSON-словарём.

    Коммит делает вызывающий код — ответ и изменения сохраняются в одной транзакции.
    """
    full_key = f"{scope}:{key}"
    # до конца транзакции второй запрос с тем же ключом ждёт здесь
    session.execute(select(func.pg_advisory_xact_lock(func.hashtext(full_key))))
    saved = session.get(IdempotencyKey, full_key)
    if saved is not None:
        return saved.response
    response = action()
    session.add(IdempotencyKey(key=full_key, response=response))
    session.flush()
    return response
