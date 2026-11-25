"""Эндпоинты модуля ops. Роутер уже подключён в app/main.py."""

from fastapi import APIRouter

router = APIRouter(prefix="", tags=["ops"])
