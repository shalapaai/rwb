/**
 * Проводит логиста от маршрутов WB к результату ночного планирования.
 * Карта и панели показывают актуальный расчёт, включая части заказов.
 */

import { useState } from "react";
import CityMap from "./CityMap";
import {
  assignmentTraces,
  describeDetour,
  detourTraces,
  formatMoney,
  legNodes,
  minutesPerDay,
  routeConnections,
  traceStyles,
  type Assignment,
  type City,
  type MapTrace,
  type Plan,
  type TraceKind,
} from "./plan-graph";

const steps = [
  { id: "routes", label: "Маршруты WB" },
  { id: "connections", label: "Пары и пересадки" },
  { id: "assignments", label: "Распределение товара" },
  { id: "detours", label: "Вставка остановок" },
  { id: "result", label: "Итог" },
] as const;

type Step = typeof steps[number]["id"];
type Connections = ReturnType<typeof routeConnections>;
const variants = ["own", "neighbor", "van", "hub"] as const;
const hours = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });

function ConnectionDetails({ connections, minGap }: { connections: Connections; minGap: number }) {
  return (
    <section className="night-plan-panel">
      <h2>Попутные пары</h2>
      <p>Товар можно забрать на более ранней остановке и довезти до более поздней.</p>
      <div className="night-plan-pairs">
        {connections.pairs.map(({ routeId, links }) => (
          <div key={routeId}>
            <h3>{routeId}</h3>
            {links.length ? (
              <ul>
                {links.map(({ from, to }, index) => <li key={index}>{from} → {to}</li>)}
              </ul>
            ) : <p>Нет попутных пар.</p>}
          </div>
        ))}
      </div>
      <h3>Пересадки между газелями</h3>
      {connections.transfers.length ? (
        <ul className="night-plan-data">
          {connections.transfers.map(({ node, from, to, gap }, index) => (
            <li key={index}>
              <strong>{node}</strong> · {from.routeId} → {to.routeId} · разрыв {gap} мин
              <span>Прибытие через {from.eta} и {to.eta} мин после выезда</span>
            </li>
          ))}
        </ul>
      ) : <p>В базовых маршрутах нет пересадок с разрывом от {minGap} мин.</p>}
    </section>
  );
}

export function OrderDetails({ orderId, parts, waiting = 0 }: {
  orderId: string;
  parts: Assignment[];
  waiting?: number;
}) {
  return (
    <div className="night-plan-order-details" aria-live="polite">
      <h3>Заказ {orderId}</h3>
      {waiting > 0 && <p>Ожидает маршрута · {waiting} шт.</p>}
      {parts.map((part, index) => (
        <div key={index} className="night-plan-order-part">
          <dl>
            <div><dt>Вариант</dt><dd>{traceStyles[part.variant].label}</dd></div>
            <div><dt>Источник</dt><dd>{part.source_node === "HUB" ? "Хаб" : part.source_node}</dd></div>
            <div><dt>Товар</dt><dd>{part.sku_id} · {part.qty} шт.</dd></div>
            <div><dt>Стоимость за штуку</dt><dd>{formatMoney(part.money_kop)}</dd></div>
            <div><dt>Срок с заказа</dt><dd>{hours.format(part.hours)} ч</dd></div>
          </dl>
          {part.variant === "hub" && !!part.legs?.length && (
            <p>Заберём с полки в хаб сегодня, доставим следующим рейсом завтра.</p>
          )}
          <h4>Плечи газелей</h4>
          {part.legs?.length ? (
            <ol className="night-plan-data">
              {part.legs.map((leg, legIndex, legs) => {
                const nextDay = part.variant === "hub" && legIndex === legs.length - 1;
                const offset = nextDay ? minutesPerDay : 0;
                return (
                  <li key={legIndex}>
                    <strong>{leg.route_id}</strong> · {leg.from_node} → {leg.to_node}
                    <span>{nextDay && "Завтра · "}{leg.depart_min - offset} → {leg.arrive_min - offset} мин после выезда</span>
                  </li>
                );
              })}
            </ol>
          ) : <p>Нет плеч попутных газелей.</p>}
        </div>
      ))}
    </div>
  );
}

export default function NightPlan({
  city,
  plan,
  selectedOrder,
  onSelectOrder,
  onSelectPvz,
}: {
  city: City;
  plan: Plan;
  selectedOrder: string | null;
  onSelectOrder: (id: string | null) => void;
  onSelectPvz?: (id: string) => void;
}) {
  const [step, setStep] = useState<Step>("routes");
  const isResult = step === "result";
  const showConnections = step === "connections" || isResult;
  const showAssignments = step === "assignments" || isResult;
  const showDetours = step === "detours" || isResult;
  const minGap = city.params?.transfer_min_gap ?? 15;
  const connections = routeConnections(city.routes, minGap);
  const plannedRoutes = new Map(plan.routes.map((route) => [route.id, route]));
  const partsByOrder = new Map<string, Assignment[]>();
  for (const part of plan.assignments) {
    const parts = partsByOrder.get(part.order_id) ?? [];
    parts.push(part);
    partsByOrder.set(part.order_id, parts);
  }
  const orderIds = [...new Set([...partsByOrder.keys(), ...Object.keys(plan.unassigned ?? {})])]
    .sort((a, b) => a.localeCompare(b, "ru", { numeric: true }));
  const traces: MapTrace[] = [];
  const legend: TraceKind[] = [];

  if (showConnections) {
    legend.push("pair", "transfer");
    for (const { routeId, links } of connections.pairs) {
      links.forEach(({ from, to }, index) => traces.push({
        id: `pair-${routeId}-${index}`,
        kind: "pair",
        label: `${routeId} · ${from} → ${to}`,
        nodes: [from, to],
        muted: isResult,
      }));
    }
    for (const node of new Set(connections.transfers.map((transfer) => transfer.node))) {
      traces.push({ id: `transfer-${node}`, kind: "transfer", label: `${node} · Пересадка`, nodes: [node] });
    }
  }
  if (isResult) {
    legend.push("movement");
    for (const [index, movement] of plan.movements.entries()) {
      movement.legs.forEach((leg, legIndex) => traces.push({
        id: `movement-${index}-${legIndex}`,
        kind: "movement",
        label: `${movement.sku_id} · ${movement.qty} шт. · ${leg.from_node} → ${leg.to_node}`,
        nodes: legNodes(leg, plannedRoutes),
      }));
    }
  }
  if (showDetours) {
    legend.push("detour");
    traces.push(...detourTraces(plan, plannedRoutes));
  }
  if (showAssignments) {
    legend.push(...variants);
    const assignments = assignmentTraces(city, plan, plannedRoutes, selectedOrder);
    traces.push(...assignments.filter((trace) => !trace.selected), ...assignments.filter((trace) => trace.selected));
  }

  return (
    <div className="night-plan">
      <div className="night-plan-steps" role="group" aria-label="Шаги ночного планирования">
        {steps.map(({ id, label }, index) => (
          <button key={id} type="button" aria-current={step === id ? "step" : undefined}
            onClick={() => setStep(id)}>
            {id !== "result" && <span>{index + 1}</span>}
            {label}
          </button>
        ))}
      </div>
      {showAssignments && (
        <section className="night-plan-panel night-plan-orders" aria-label="Назначения заказов">
          <h2>Заказы</h2>
          {orderIds.length ? (
            <div className="night-plan-order-list">
              {orderIds.map((orderId) => {
                const parts = partsByOrder.get(orderId) ?? [];
                const kinds = [...new Set(parts.map((part) => part.variant))];
                return (
                  <button
                    key={orderId}
                    type="button"
                    title={kinds.map((kind) => traceStyles[kind].label).join(", ")}
                    aria-pressed={selectedOrder === orderId}
                    onClick={() => onSelectOrder(selectedOrder === orderId ? null : orderId)}
                  >
                    {kinds.map((kind) => (
                      <span key={kind} aria-hidden="true" style={{ background: traceStyles[kind].color }} />
                    ))}
                    {orderId}
                  </button>
                );
              })}
            </div>
          ) : <p>В плане нет назначений.</p>}
          {selectedOrder && <OrderDetails orderId={selectedOrder} parts={partsByOrder.get(selectedOrder) ?? []}
            waiting={plan.unassigned?.[selectedOrder]} />}
        </section>
      )}
      <CityMap city={isResult ? { ...city, routes: plan.routes } : city}
        traces={traces} legend={legend} onSelectPvz={onSelectPvz} />
      {showDetours && (
        <section className="night-plan-panel">
          <h2>Вставленные остановки</h2>
          {plan.inserted_stops.length ? (
            <ul className="night-plan-data">
              {plan.inserted_stops.map((stop) => (
                <li key={`${stop.route_id}-${stop.node_id}`}>
                  <strong>{stop.route_id} · {stop.node_id}</strong>
                  <span>{describeDetour(stop)}</span>
                </li>
              ))}
            </ul>
          ) : <p>Ночной план сохранил базовые остановки.</p>}
        </section>
      )}
      {isResult && (
        <section className="night-plan-panel">
          <h2>Переброски под прогноз</h2>
          {plan.movements.length ? (
            <ul className="night-plan-data">
              {plan.movements.map((movement, index) => (
                <li key={index}>
                  <strong>{movement.from_node} → {movement.to_node}</strong>
                  <span>{movement.sku_id} · {movement.qty} шт. · {formatMoney(movement.money_kop)} за штуку</span>
                </li>
              ))}
            </ul>
          ) : <p>В плане нет перебросок.</p>}
          <dl className="city-map-costs">
            <div><dt>Стоимость ночного плана</dt><dd>{formatMoney(plan.total_money_kop)}</dd></div>
            <div><dt>Обычные товары через хаб</dt><dd>{formatMoney(plan.hub_money_kop)}</dd></div>
          </dl>
        </section>
      )}
      {showConnections && <ConnectionDetails connections={connections} minGap={minGap} />}
    </div>
  );
}
