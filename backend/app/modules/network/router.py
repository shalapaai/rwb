"""Эндпоинты модуля network. Роутер уже подключён в app/main.py."""

from fastapi import APIRouter

router = APIRouter(prefix="", tags=["network"])
