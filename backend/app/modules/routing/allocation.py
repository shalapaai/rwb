"""Согласует целочисленные потоки артикулов по общим ёмкостям.

Потоки без конфликтов сразу образуют оптимальный план для данного графа.
При конфликте поиск ограничивает расход ресурса на отдельных рёбрах.
"""

from collections import defaultdict
from collections.abc import Hashable
from dataclasses import dataclass
from decimal import Decimal
from heapq import heappop, heappush
from itertools import count

import networkx as nx

from app.modules.routing.network import HUB, KEEP, UNASSIGNED, Capacity, Resource

type Edge = tuple[Hashable, Hashable]
type Limits = frozenset[tuple[Edge, int]]
type Flow = dict[Hashable, dict[Hashable, int]]


@dataclass
class FlowSolution:
    limits: Limits
    flow: Flow
    waiting: int
    cost: int
    usage: dict[Resource, list[tuple[Edge, int, Decimal]]]


def _solve(graph: nx.DiGraph, limits: Limits) -> FlowSolution:
    if limits:
        graph = graph.copy()
        for (source, dest), limit in limits:
            graph[source][dest]["capacity"] = limit
    flow = nx.min_cost_flow(graph)
    waiting = sum(qty for dest, qty in flow[UNASSIGNED].items() if dest not in (KEEP, HUB))
    penalty = sum(
        graph[UNASSIGNED][dest]["weight"] * qty
        for dest, qty in flow[UNASSIGNED].items()
    )
    usage = defaultdict(list)
    for source, dest, data in graph.edges(data=True):
        qty = flow[source][dest]
        if qty and "resource" in data:
            usage[data["resource"]].append(((source, dest), qty, data["units"]))
    return FlowSolution(limits, flow, waiting, nx.cost_of_flow(graph, flow) - penalty, usage)


def allocate_flows(graphs: list[nx.DiGraph], cap: Capacity) -> list[Flow]:
    """Находит лучший совместимый набор потоков и списывает использованную ёмкость."""
    initial = tuple(_solve(graph, frozenset()) for graph in graphs)
    cache = [{solution.limits: solution} for solution in initial]
    visited = {tuple(solution.limits for solution in initial)}
    serial = count()
    queue = []

    def push(solutions: tuple[FlowSolution, ...]) -> None:
        # Независимые решения задают нижнюю оценку для всех планов этой ветви.
        rank = (sum(s.waiting for s in solutions), sum(s.cost for s in solutions))
        heappush(queue, (*rank, next(serial), solutions))

    push(initial)
    while queue:
        *_, solutions = heappop(queue)
        usage = defaultdict(list)
        for index, solution in enumerate(solutions):
            for resource, edges in solution.usage.items():
                usage[resource].extend((index, *edge) for edge in edges)

        totals = {
            resource: sum(qty * units for _, _, qty, units in edges)
            for resource, edges in usage.items()
        }
        conflict = next((r for r, used in totals.items() if used > cap.remaining(r)), None)
        if conflict is None:
            for resource, used in totals.items():
                cap.consume(resource, used)
            return [solution.flow for solution in solutions]

        edges = usage[conflict]
        excess = totals[conflict] - cap.remaining(conflict)
        # В любом допустимом плане хотя бы одно из занятых рёбер отдаёт свою долю избытка.
        for index, edge, qty, units in reversed(edges):
            quotient, remainder = divmod(excess, units * len(edges))
            limit = qty - int(quotient) - bool(remainder)
            if limit < 0:
                continue
            limits = dict(solutions[index].limits)
            limits[edge] = limit
            scope = frozenset(limits.items())
            key = tuple(scope if i == index else s.limits for i, s in enumerate(solutions))
            if key in visited:
                continue
            visited.add(key)
            if scope not in cache[index]:
                cache[index][scope] = _solve(graphs[index], scope)
            push((*solutions[:index], cache[index][scope], *solutions[index + 1:]))

    raise RuntimeError("Не удалось согласовать ёмкости потоков")
