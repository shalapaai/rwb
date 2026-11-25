"""Честное сравнение на одних и тех же заказах (критерий «выгода vs бейзлайн»).

Три схемы:
  * all_hub  — всё через хаб и магистраль (как сейчас);
  * greedy   — заказы по одному, каждый берёт лучший вариант на момент прихода;
               маршруты не правятся, переброски нет;
  * nightly  — полный ночной план: все заказы сразу (поток), вставка остановок,
               переброска под прогноз.
"""

from app.modules.routing.daily import choose, day_state
from app.modules.routing.models import Assignment, City, Order, SchemeSummary, Variant
from app.modules.routing.network import Capacity
from app.modules.routing.nightly import nightly_plan


def _summary(
    name: str, assignments: list[Assignment], extra_kop: int = 0, added_km: float = 0.0
) -> SchemeSummary:
    units = sum(a.qty for a in assignments) or 1
    money = sum(a.money_kop * a.qty for a in assignments) + extra_kop
    hub_units = sum(a.qty for a in assignments if a.variant == Variant.HUB)
    return SchemeSummary(
        scheme=name,
        money_kop=money,
        money_per_unit_kop=round(money / units),
        avg_hours=round(sum(a.hours * a.qty for a in assignments) / units, 2),
        local_share=round(1 - hub_units / units, 3),
        added_km=round(added_km, 2),
        hub_units=hub_units,
    )


def _hub(o: Order, city: City) -> Assignment:
    return Assignment(
        order_id=o.id,
        sku_id=o.sku_id,
        qty=o.qty,
        variant=Variant.HUB,
        source_node="HUB",
        money_kop=city.params.hub_kop,
        hours=city.params.hub_hours,
    )


def all_hub(city: City) -> SchemeSummary:
    return _summary("Всё через хаб", [_hub(o, city) for o in city.orders])


def greedy(city: City) -> SchemeSummary:
    nodes = {n.id: n for n in city.nodes}
    stock: dict[tuple[str, str], int] = {}
    for s in city.stock:
        stock[(s.node_id, s.sku_id)] = stock.get((s.node_id, s.sku_id), 0) + s.qty
    cap = Capacity.from_routes(city.routes, nodes, city.params)
    st = day_state(city, city.routes, cap, stock, [])
    for o in city.orders:
        if choose(st, o).assignment is None:
            # ни один вариант не успевает к сроку — заказ всё равно исполняется через хаб,
            # иначе он выпал бы из сравнения и исказил ₽ на единицу
            st.assignments.append(_hub(o, city))
    return _summary("По одному заказу (жадно)", st.assignments)


def nightly(city: City) -> SchemeSummary:
    """Деньги — только по заказам и крюкам; переброска под прогноз — вложение в завтра,
    её стоимость показана в плане отдельно."""
    plan, _ = nightly_plan(city)
    detours = sum(i.cost_kop for i in plan.inserted_stops)
    km = sum(i.added_km for i in plan.inserted_stops)
    return _summary("Ночной план", plan.assignments, extra_kop=detours, added_km=km)


def compare(city: City) -> list[SchemeSummary]:
    return [all_hub(city), greedy(city), nightly(city)]
