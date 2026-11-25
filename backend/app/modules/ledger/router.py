"""Эндпоинты модуля ledger. Роутер уже подключён в app/main.py."""

from fastapi import APIRouter

router = APIRouter(prefix="/ledger", tags=["ledger"])
