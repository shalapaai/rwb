"""Эндпоинты модуля analytics. Роутер уже подключён в app/main.py."""

from fastapi import APIRouter

router = APIRouter(prefix="/analytics", tags=["analytics"])
