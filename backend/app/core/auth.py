"""Роли и проверка прав.

Роль передаётся заголовком `X-Role` из переключателя ролей в UI —
организаторы разрешают его вместо авторизации. JWT — в плане развития.
"""

from enum import StrEnum
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status


class Role(StrEnum):
    PVZ_OPERATOR = "pvz_operator"
    SELLER = "seller"
    LOGIST = "logist"
    ADMIN = "admin"


def current_role(x_role: Annotated[str | None, Header()] = None) -> Role:
    try:
        return Role(x_role)
    except ValueError:
        detail = "Не указана или неизвестна роль"
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail) from None


def require_roles(*allowed: Role):
    """Зависимость эндпоинта: `Depends(require_roles(Role.ADMIN))`."""

    def checker(role: Annotated[Role, Depends(current_role)]) -> Role:
        if role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
        return role

    return checker
