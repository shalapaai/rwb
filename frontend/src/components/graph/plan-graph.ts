/**
 * Подготавливает связи и линии для схемы ночного планирования.
 * Пары и пересадки выводятся из базовых рейсов, остальные данные даёт API.
 */

import type { components } from "../../api-types";

type Schemas = components["schemas"];
export type City = Schemas["City"];
export type Plan = Schemas["Plan"];
export type Assignment = Schemas["Assignment"];
export type Leg = Schemas["Leg"];

export const traceStyles = {
  pair: { label: "Попутная пара", color: "#7649a0" },
  transfer: { label: "Пересадка", color: "#a62168" },
  own: { label: "Своя полка", color: "#08734d" },
  neighbor: { label: "Соседний ПВЗ", color: "#88550a" },
  van: { label: "Попутная газель", color: "#1752b1" },
  hub: { label: "Через хаб", color: "#526077" },
  detour: { label: "Крюк газели", color: "#ad3919" },
  movement: { label: "Переброска под прогноз", color: "#007568" },
};

export type TraceKind = keyof typeof traceStyles;
export type MapTrace = {
  id: string;
  kind: TraceKind;
  nodes: string[];
  label: string;
  muted?: boolean;
  selected?: boolean;
};

const rubles = new Intl.NumberFormat("ru-RU", {
  style: "currency",
  currency: "RUB",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatMoney(kopecks: number) {
  return rubles.format(kopecks / 100);
}

export function routeConnections(routes: City["routes"], minGap: number) {
  const arrivals = new Map<string, { routeId: string; eta: number }[]>();
  const pairs = routes.map((route) => ({
    routeId: route.id,
    links: route.stops.flatMap((from, index) => {
      const atNode = arrivals.get(from.node_id) ?? [];
      atNode.push({ routeId: route.id, eta: from.eta_min });
      arrivals.set(from.node_id, atNode);
      return route.stops.slice(index + 1)
        .filter((to) => to.node_id !== from.node_id)
        .map((to) => ({ from: from.node_id, to: to.node_id }));
    }),
  }));
  const transfers = [...arrivals].flatMap(([node, visits]) =>
    visits.flatMap((from) =>
      visits
        .filter((to) => to.routeId !== from.routeId && to.eta - from.eta >= minGap)
        .map((to) => ({ node, from, to, gap: to.eta - from.eta })),
    ),
  );
  return { pairs, transfers };
}

/** Плечо может проходить через несколько остановок одной газели. */
export function legNodes(leg: Leg, routes: Map<string, City["routes"][number]>): string[] {
  const stops = routes.get(leg.route_id)?.stops ?? [];
  const from = stops.findIndex((stop) => stop.node_id === leg.from_node && stop.eta_min === leg.depart_min);
  const to = stops.findIndex((stop, index) => (
    index > from && stop.node_id === leg.to_node && stop.eta_min === leg.arrive_min
  ));
  return from >= 0 && to > from
    ? stops.slice(from, to + 1).map((stop) => stop.node_id)
    : [leg.from_node, leg.to_node];
}

export function assignmentTraces(
  city: City,
  plan: Plan,
  routes: Map<string, City["routes"][number]>,
  selectedOrder: string | null,
): MapTrace[] {
  const orders = new Map(city.orders?.map((order) => [order.id, order]));
  return plan.assignments.flatMap((assignment, index) => {
    const { order_id: id, variant, source_node: source, legs } = assignment;
    const label = `${id} · ${traceStyles[variant].label}`;
    const view = { kind: variant, label, selected: selectedOrder === id, muted: selectedOrder !== null && selectedOrder !== id };
    if (legs?.length) {
      return legs.map((leg, legIndex) => ({
        ...view,
        id: `order-${index}-${legIndex}`,
        nodes: legNodes(leg, routes),
      }));
    }
    // Без плеч самовывоз отмечаем в ПВЗ выдачи, а базовую доставку — связью хаб → ПВЗ.
    const destination = orders.get(id)?.pvz_id;
    const nodes = variant === "hub" && destination ? [source, destination] : [source];
    return [{ ...view, id: `order-${index}`, nodes }];
  });
}

export function describeDetour(stop: Plan["inserted_stops"][number]) {
  return `+${stop.added_min} мин · крюк ${formatMoney(stop.cost_kop)} · выгода ${formatMoney(stop.gain_kop)}`;
}

export function detourTraces(plan: Plan, routes: Map<string, City["routes"][number]>): MapTrace[] {
  return plan.inserted_stops.flatMap((stop, index) => {
    const label = `${stop.node_id} · ${describeDetour(stop)}`;
    const pin: MapTrace = { id: `detour-${index}-pin`, kind: "detour", nodes: [stop.node_id], label };
    const route = routes.get(stop.route_id);
    const position = route?.stops.findIndex((visit) => visit.node_id === stop.node_id) ?? -1;
    if (!route || position < 0) return [pin];
    const before = route.stops[position - 1]?.node_id ?? route.depot_id;
    const after = route.stops[position + 1]?.node_id ?? route.depot_id;
    return [pin, { ...pin, id: `detour-${index}`, nodes: [before, stop.node_id, after] }];
  });
}
