# Предметные модули

Пусто намеренно: предметная логика пишется на хакатоне (п. 8.1 положения).

Планируемые модули и их ответственность — в [docs/03-architecture.md](../../../docs/03-architecture.md#модули-backend):
`network`, `inventory`, `orders`, `routing`, `ledger`, `ops`, `stubs`.

Соглашения для каждого модуля:

- `models.py` — ORM-модели (наследуются от `app.core.db.Base`);
- `schemas.py` — Pydantic-схемы запросов и ответов;
- `service.py` — бизнес-логика без FastAPI;
- `router.py` — эндпоинты, подключаются в `app/main.py`;
- тесты — в `backend/tests/<модуль>/`.

Деньги — только в копейках (`int`), начисления — только через одну функцию проводки.
