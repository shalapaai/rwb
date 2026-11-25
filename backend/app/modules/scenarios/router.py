"""Эндпоинты модуля scenarios. Роутер уже подключён в app/main.py."""

from fastapi import APIRouter

router = APIRouter(prefix="/scenarios", tags=["scenarios"])
