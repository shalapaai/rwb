"""Эндпоинты модуля orders. Роутер уже подключён в app/main.py."""

from fastapi import APIRouter

router = APIRouter(prefix="/orders", tags=["orders"])
