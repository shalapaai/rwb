"""Правило «оставлять ли отказ этого товара на mesh-полке» — без БД, чтобы проверять отдельно.

Отказ остаётся в городе, только если товар разрешён для mesh и его покупают достаточно
часто. «Достаточно часто» — не выдуманное число: это порог спроса из модели
(/model/boundary), ниже которого mesh обходится дороже хаба. Иначе — в хаб с причиной.
Запреты — решение команды 26.11: крупногабарит (больше MAX_MESH_UNIT_L = 10 л, как в N4 и сиде
NK1) и маркированные товары.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.modules.inventory.models import MAX_MESH_UNIT_L as MAX_VOLUME_L  # один лимит с N4


@dataclass(frozen=True)
class ProductFacts:
    volume_l: float
    product_allowed: bool  # products.mesh_allowed
    category_allowed: bool  # categories.mesh_allowed
    category_ban_reason: str | None
    marked: bool  # «Честный знак»


def _num(x: Decimal) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".").replace(".", ",")


def decide(facts: ProductFacts, demand_per_day: Decimal, threshold: Decimal) -> tuple[bool, str]:
    """(можно ли в mesh, причина). Запреты проверяются раньше спроса: популярность их не снимает."""
    if not facts.category_allowed:
        return False, f"категория запрещена: {facts.category_ban_reason or 'не для mesh'}"
    if facts.marked:
        return False, "маркированный товар («Честный знак»)"
    if facts.volume_l > MAX_VOLUME_L:
        return False, f"больше {MAX_VOLUME_L:g} л"
    if not facts.product_allowed:
        return False, "товар не допущен на mesh-полку"
    if demand_per_day < threshold:
        return False, (
            f"спрос {_num(demand_per_day)}/сут ниже порога {_num(threshold)}/сут — mesh дороже хаба"
        )
    return True, f"спрос {_num(demand_per_day)}/сут не ниже порога {_num(threshold)}/сут"
