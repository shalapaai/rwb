"""Схемы ответа GET /network для всех ролей.

Координаты передаются в километрах, объёмы полок — в литрах.
"""

from pydantic import BaseModel

from app.modules.routing.models import NodeKind, VanRoute


class NetworkNode(BaseModel):
    id: str
    kind: NodeKind
    name: str
    x_km: float
    y_km: float
    is_open: bool
    storage_total_l: float
    mesh_share_pct: int
    ops_limit: int
    shelf_used_l: float
    shelf_limit_l: float


class NetworkResponse(BaseModel):
    nodes: list[NetworkNode]
    routes: list[VanRoute]
