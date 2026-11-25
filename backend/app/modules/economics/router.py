"""Эндпоинты модуля economics. Роутер уже подключён в app/main.py."""

from fastapi import APIRouter

router = APIRouter(prefix="/model", tags=["economics"])
