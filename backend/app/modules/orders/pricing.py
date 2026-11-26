"""Ставки для расчётов при продаже и выдаче заказа.
Значения берутся из общего каталога economics.params для обеих схем исполнения.
Процент применяется к сумме продажи с одним округлением до копейки.
"""

from app.modules.economics.params import DEFAULTS as P

COMMISSION_PCT = P.commission_pct
ISSUE_FEE_PCT = P.issue_fee_pct
SELLER_LOGISTICS_BASE_KOP = P.seller_logistics_base_kop
SELLER_LOGISTICS_MESH_KOP = P.seller_logistics_mesh_kop


def pct(price_kop: int, percent: int) -> int:
    """Процент от цены в целых копейках, округление половины вверх."""
    return (price_kop * percent + 50) // 100
