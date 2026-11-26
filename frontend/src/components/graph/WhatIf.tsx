/**
 * Редактирует заявки для ночного планирования.
 * Показывает рассчитанные назначения и вставленные остановки на общей карте.
 */

import { useState, type FormEvent } from "react";
import CityMap from "./CityMap";
import { OrderDetails } from "./NightPlan";
import {
  assignmentTraces,
  describeDetour,
  detourTraces,
  formatMoney,
  type City,
  type Order,
  type Plan,
} from "./plan-graph";

const uniquePrefix = "UNIQUE-";

function OrderForm({
  city, source, busy, onAdd,
}: {
  city: City;
  source: string;
  busy: boolean;
  onAdd: (draft: Omit<Order, "id">) => void;
}) {
  const shelf = new Map<string, number>();
  for (const item of city.stock) {
    if (item.node_id === source && item.qty > 0) {
      shelf.set(item.sku_id, (shelf.get(item.sku_id) ?? 0) + item.qty);
    }
  }
  const [sku, setSku] = useState(shelf.keys().next().value ?? "");
  const [dest, setDest] = useState("");
  const [qty, setQty] = useState("1");
  const unique = sku.startsWith(uniquePrefix);
  const requested = (city.orders ?? []).reduce((total, order) => (
    order.sku_id === sku ? total + order.qty : total
  ), 0);
  const available = Math.max(0, (shelf.get(sku) ?? 0) - requested);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const quantity = Number(qty);
    if (sku && dest && Number.isSafeInteger(quantity) && quantity > 0) {
      if (unique && (dest === source || quantity > available)) return;
      onAdd({ sku_id: sku, pvz_id: dest, qty: quantity, accepts_neighbor: false });
    }
  }

  return (
    <>
      <p>Mesh-полка <strong>{source}</strong></p>
      {shelf.size ? (
        <form className="what-if-form" onSubmit={submit}>
          <label>
            Товар на mesh-полке
            <select value={sku} onChange={(event) => {
              const nextSku = event.target.value;
              setSku(nextSku);
              setQty("1");
              if (nextSku.startsWith(uniquePrefix) && dest === source) setDest("");
            }} disabled={busy}>
              {[...shelf].map(([id, stock]) => (
                <option key={id} value={id}>
                  {id.startsWith(uniquePrefix) ? `Уникальный товар ${source}` : id} · {stock} шт.
                </option>
              ))}
            </select>
          </label>
          <div className="what-if-form-row">
            <label>
              ПВЗ выдачи
              <select required value={dest} onChange={(event) => setDest(event.target.value)} disabled={busy}>
                <option value="">Выберите ПВЗ</option>
                {city.nodes.filter((node) => node.kind === "pvz" && (!unique || node.id !== source)).map(({ id }) => (
                  <option key={id} value={id}>{id}</option>
                ))}
              </select>
            </label>
            <label>
              Штук
              <input type="number" min={1} max={unique ? available : undefined} step={1} required
                value={qty} disabled={busy || (unique && available === 0)}
                onChange={(event) => setQty(event.target.value)} />
            </label>
          </div>
          <button type="submit" disabled={busy || !dest || (unique && available === 0)}>
            Добавить и пересчитать
          </button>
          <p className="what-if-hint">{unique
            ? `Товар есть только в ${source}. Доступно для новых заявок ${available} шт.`
            : "Источник товара выберет алгоритм по остаткам всего города."}</p>
        </form>
      ) : <p>На mesh-полке этого ПВЗ нет товаров.</p>}
    </>
  );
}

export default function WhatIf({
  city, plan, source, onSelectPvz, selectedOrder, onSelectOrder, busy, onAdd, onRemove,
}: {
  city: City;
  plan: Plan;
  source: string | null;
  onSelectPvz: (id: string) => void;
  selectedOrder: string | null;
  onSelectOrder: (id: string | null) => void;
  busy: boolean;
  onAdd: (draft: Omit<Order, "id">) => void;
  onRemove: (id: string) => void;
}) {
  const orders = city.orders ?? [];
  const plannedRoutes = new Map(plan.routes.map((route) => [route.id, route]));
  const assignments = assignmentTraces(city, plan, plannedRoutes, selectedOrder);
  const traces = [
    ...detourTraces(plan, plannedRoutes),
    ...assignments.filter((trace) => !trace.selected),
    ...assignments.filter((trace) => trace.selected),
  ];

  return (
    <div className="what-if-view">
      <aside className="night-plan-panel what-if-panel" aria-label="Куда нужно товару">
        <h2>Добавить заявку</h2>
        {source ? (
          <OrderForm key={source} city={city} source={source} busy={busy} onAdd={onAdd} />
        ) : <p>Выберите ПВЗ на схеме, чтобы посмотреть товары на его полке.</p>}
        <section className="what-if-section">
          <h3>Заявки на доставку ({orders.length})</h3>
          {orders.length ? (
            <ul className="what-if-orders">
              {orders.map((order) => (
                <li key={order.id}>
                  <button type="button" aria-pressed={selectedOrder === order.id}
                    onClick={() => onSelectOrder(selectedOrder === order.id ? null : order.id)}>
                    <strong>{order.id}</strong>
                    <span>{order.sku_id.startsWith(uniquePrefix)
                      ? `Уникальный товар · ${order.sku_id.slice(uniquePrefix.length)}`
                      : order.sku_id} · {order.qty} шт. → {order.pvz_id}</span>
                    {!!plan.unassigned?.[order.id] && (
                      <span>Ожидает маршрута · {plan.unassigned[order.id]} шт.</span>
                    )}
                  </button>
                  <button type="button" className="what-if-remove" disabled={busy}
                    aria-label={`Удалить заявку ${order.id}`} onClick={() => onRemove(order.id)}>
                    Удалить
                  </button>
                </li>
              ))}
            </ul>
          ) : <p>Заявок нет. Добавьте товар и ПВЗ выдачи.</p>}
        </section>
        {selectedOrder && <OrderDetails orderId={selectedOrder}
          parts={plan.assignments.filter((part) => part.order_id === selectedOrder)}
          waiting={plan.unassigned?.[selectedOrder]} />}
        {plan.inserted_stops.length > 0 && (
          <section className="what-if-section">
            <h3>Добавленные остановки</h3>
            <ul className="night-plan-data">
              {plan.inserted_stops.map((stop) => (
                <li key={`${stop.route_id}-${stop.node_id}`}>
                  <strong>{stop.route_id} · {stop.node_id}</strong>
                  <span>{describeDetour(stop)}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
        <dl className="what-if-metrics">
          <div><dt>Стоимость плана</dt><dd>{formatMoney(plan.total_money_kop)}</dd></div>
          <div><dt>Обычные товары через хаб</dt><dd>{formatMoney(plan.hub_money_kop)}</dd></div>
        </dl>
      </aside>
      <CityMap city={{ ...city, routes: plan.routes }} traces={traces}
        legend={[...new Set(traces.map((trace) => trace.kind))]}
        selectedPvz={source} onSelectPvz={onSelectPvz} />
    </div>
  );
}
