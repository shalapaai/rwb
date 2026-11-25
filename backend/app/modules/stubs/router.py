"""Эндпоинты модуля stubs. Роутер уже подключён в app/main.py."""

from fastapi import APIRouter

router = APIRouter(prefix="/stub", tags=["stubs"])
