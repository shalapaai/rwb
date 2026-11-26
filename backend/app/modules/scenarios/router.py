"""Сценарии защиты: сброс демо-города и запуск сценариев 1–5 (роль «Сценарии» = admin)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Role, require_roles
from app.core.db import get_session
from app.core.idempotency import IdemKey, run_once
from app.modules.scenarios import service

router = APIRouter(prefix="/scenarios", tags=["scenarios"])

Admin = Annotated[Role, Depends(require_roles(Role.ADMIN))]
AnyRole = Annotated[Role, Depends(require_roles(*Role))]
Db = Annotated[Session, Depends(get_session)]


@router.get("")
def titles(_: AnyRole) -> dict[int, str]:
    """Список сценариев."""
    return service.TITLES


@router.post("/reset")
def reset(_: Admin, db: Db, key: IdemKey) -> dict[str, Any]:
    """Чистая БД и демо-город. Ключ принимаем, но не храним: сброс стирает и ключи,
    а повторный сброс и так даёт то же состояние."""
    state = service.reset(db)
    db.commit()
    return state


@router.post("/{n}/run")
def run(n: int, _: Admin, db: Db, key: IdemKey) -> dict[str, Any]:
    """Шаги сценария через те же сервисы, что и API; «до / после» и проверки."""
    result = run_once(db, f"scenario:{n}", key, lambda: service.run(db, n))
    db.commit()
    return result
