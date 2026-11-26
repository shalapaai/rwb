"""Чтение сети ПВЗ и маршрутов газелей из БД.

Возвращает текущую загрузку mesh-полок и лимиты из настроек ПВЗ.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.core.auth import current_role
from app.core.db import get_session
from app.modules.inventory.models import Product, Unit
from app.modules.network.models import Node, VanStop
from app.modules.network.models import VanRoute as DbVanRoute
from app.modules.network.schemas import NetworkNode, NetworkResponse
from app.modules.routing.models import NodeKind, Stop, VanRoute

router = APIRouter(prefix="", tags=["network"])


@router.get("/network", dependencies=[Depends(current_role)])
def get_network(session: Annotated[Session, Depends(get_session)]) -> NetworkResponse:
    # Резерв ещё занимает место на полке до отправки или выдачи.
    stock = (
        select(Unit.node_id, func.sum(Product.volume_l).label("used_l"))
        .join(Product, Product.sku == Unit.sku)
        .where(Unit.status.in_(("on_shelf", "reserved")))
        .group_by(Unit.node_id)
        .subquery()
    )
    try:
        nodes = session.execute(
            select(Node, func.coalesce(stock.c.used_l, 0.0))
            .outerjoin(stock, and_(Node.id == stock.c.node_id, Node.kind == NodeKind.PVZ))
            .order_by(Node.id)
        )
        route_stops = session.execute(
            select(DbVanRoute, VanStop)
            .outerjoin(VanStop, VanStop.route_id == DbVanRoute.id)
            .order_by(DbVanRoute.id, VanStop.seq)
        )
    except ProgrammingError as exc:
        if getattr(exc.orig, "sqlstate", None) == "42P01":
            raise HTTPException(503, "Таблицы сети ещё не созданы миграцией N1") from exc
        raise

    routes: dict[str, VanRoute] = {}
    for route, stop in route_stops:
        if route.id not in routes:
            routes[route.id] = VanRoute(
                id=route.id, depot_id=route.depot_id, capacity=route.capacity, stops=[]
            )
        if stop is not None:
            routes[route.id].stops.append(Stop.model_validate(stop, from_attributes=True))

    return NetworkResponse(
        nodes=[
            NetworkNode(
                id=node.id,
                kind=NodeKind(node.kind),
                name=node.name,
                x_km=node.x_km,
                y_km=node.y_km,
                is_open=node.is_open,
                storage_total_l=node.storage_total_l,
                mesh_share_pct=node.mesh_share_pct,
                ops_limit=node.ops_limit,
                shelf_used_l=used_l,
                shelf_limit_l=node.mesh_limit_l if node.kind == NodeKind.PVZ else 0,
            )
            for node, used_l in nodes
        ],
        routes=list(routes.values()),
    )
