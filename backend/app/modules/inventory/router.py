"""Эндпоинты модуля inventory. Роутер уже подключён в app/main.py."""

from fastapi import APIRouter

router = APIRouter(prefix="/pvz", tags=["inventory"])
