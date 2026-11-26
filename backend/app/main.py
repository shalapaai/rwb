from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import system
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.modules import all_models  # noqa: F401  — все таблицы в метаданных
from app.modules.analytics.router import router as analytics_router
from app.modules.economics.router import router as economics_router
from app.modules.inventory.router import router as inventory_router
from app.modules.ledger.router import router as ledger_router
from app.modules.network.router import router as network_router
from app.modules.ops.router import router as ops_router
from app.modules.orders.router import router as orders_router
from app.modules.routing.router import router as routing_router
from app.modules.scenarios.router import router as scenarios_router
from app.modules.stubs.router import router as stubs_router

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

# Предметные роутеры: все модули уже подключены, новые строки сюда добавлять не нужно.
app.include_router(analytics_router)
app.include_router(economics_router)
app.include_router(inventory_router)
app.include_router(ledger_router)
app.include_router(network_router)
app.include_router(ops_router)
app.include_router(orders_router)
app.include_router(scenarios_router)
app.include_router(stubs_router)
app.include_router(routing_router)
