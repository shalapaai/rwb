from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import system
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.modules.routing.router import router as routing_router

settings = get_settings()
setup_logging(settings.log_level)

app = FastAPI(
    title=settings.app_name,
    description="Mesh-доставка с хранением в ПВЗ. Заглушки внешних систем — под префиксом /stub.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def count_requests(request: Request, call_next):
    system.count_request()
    return await call_next(request)


app.include_router(system.router)

# Предметные роутеры подключаются здесь, по одному на модуль из app/modules.
app.include_router(routing_router)
