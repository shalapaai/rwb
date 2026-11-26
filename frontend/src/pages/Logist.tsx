import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { paths } from "../api-types";
import NightPlan from "../components/graph/NightPlan";
import WhatIf from "../components/graph/WhatIf";
import type { City, Order, Plan } from "../components/graph/plan-graph";

type RoutingDemo = paths["/routing/demo"]["get"]["responses"][200]["content"]["application/json"];
type Workspace = { city: City; plan: Plan };
type LoadState = { status: "loading" | "error" } | { status: "ready"; workspace: Workspace };
const orderStorageKey = "logist.nightly-orders.v1";

function loadOrders(city: City): Order[] {
  try {
    const stored = sessionStorage.getItem(orderStorageKey);
    if (stored !== null) {
      const orders: Order[] = JSON.parse(stored);
      const skus = new Set(city.skus.map((sku) => sku.id));
      const destinations = new Set(city.nodes.filter((node) => node.kind === "pvz").map((node) => node.id));
      if (Array.isArray(orders) && orders.every((order) => (
        typeof order?.id === "string" && skus.has(order.sku_id) && destinations.has(order.pvz_id)
        && Number.isSafeInteger(order.qty) && (order.qty ?? 0) > 0
      )) && new Set(orders.map((order) => order.id)).size === orders.length) return orders;
    }
  } catch {
    // При недоступном хранилище исходный сценарий всё равно можно открыть.
  }
  return city.orders ?? [];
}

function planCity(city: City, signal: AbortSignal) {
  return api<Plan>("/routing/plan", "logist", { method: "POST", body: JSON.stringify(city), signal });
}

function RoutingWorkspace({ initial }: { initial: Workspace }) {
  const [mode, setMode] = useState<"plan" | "what-if">("plan");
  const [source, setSource] = useState<string | null>(null);
  const [selectedOrder, setSelectedOrder] = useState<string | null>(null);
  const [{ city, plan }, setWorkspace] = useState(initial);
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const pending = useRef<AbortController | null>(null);
  const orders = city.orders ?? [];

  useEffect(() => () => pending.current?.abort(), []);

  async function recalculate(nextOrders: Order[], nextSelection: string | null) {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setStatus("loading");
    const nextCity = { ...city, orders: nextOrders };
    try {
      const nextPlan = await planCity(nextCity, controller.signal);
      if (controller.signal.aborted) return;
      setWorkspace({ city: nextCity, plan: nextPlan });
      setSelectedOrder(nextSelection);
      setStatus("idle");
      try {
        sessionStorage.setItem(orderStorageKey, JSON.stringify(nextOrders));
      } catch {
        // Сценарий доступен до закрытия экрана, даже если хранение отключено.
      }
    } catch {
      if (!controller.signal.aborted) setStatus("error");
    } finally {
      pending.current = null;
    }
  }

  function addOrder(draft: Omit<Order, "id">) {
    const ids = new Set(orders.map((order) => order.id));
    let number = 1;
    while (ids.has(`O-${number}`)) number += 1;
    const id = `O-${number}`;
    void recalculate([...orders, { ...draft, id }], id);
  }

  return (
    <>
      <div className="night-plan-steps logist-modes" role="group" aria-label="Режим схемы">
        <button type="button" aria-pressed={mode === "plan"} onClick={() => setMode("plan")}>
          Ночной план
        </button>
        <button type="button" aria-pressed={mode === "what-if"} onClick={() => setMode("what-if")}>
          Куда нужно товару
        </button>
      </div>
      {status === "loading" && <p role="status">Пересчитываем ночной план…</p>}
      {status === "error" && <p role="alert">Не удалось пересчитать план. Повторите действие.</p>}
      <div hidden={mode !== "plan"}>
        <NightPlan city={city} plan={plan} selectedOrder={selectedOrder}
          onSelectOrder={setSelectedOrder} onSelectPvz={(pvz) => {
            setSource(pvz);
            setMode("what-if");
          }} />
      </div>
      {mode === "what-if" && (
        <WhatIf city={city} plan={plan} source={source} onSelectPvz={setSource}
          selectedOrder={selectedOrder} onSelectOrder={setSelectedOrder}
          busy={status === "loading"} onAdd={addOrder}
          onRemove={(id) => void recalculate(orders.filter((order) => order.id !== id),
            selectedOrder === id ? null : selectedOrder)} />
      )}
    </>
  );
}

/** Логист просматривает ночной план и примеряет маршруты отдельных товаров. */
export default function Logist() {
  const [load, setLoad] = useState<LoadState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api<RoutingDemo>("/routing/demo", "logist", { signal: controller.signal })
      .then(async (demo) => {
        if (controller.signal.aborted) return;
        const city = { ...demo.city, orders: loadOrders(demo.city) };
        const plan = await planCity(city, controller.signal);
        if (!controller.signal.aborted) setLoad({ status: "ready", workspace: { city, plan } });
      })
      .catch(() => {
        if (!controller.signal.aborted) setLoad({ status: "error" });
      });
    return () => controller.abort();
  }, [attempt]);

  return (
    <section className="city-map-page" aria-labelledby="city-map-heading">
      <div className="city-map-heading">
        <h1 id="city-map-heading">Схема города</h1>
        <p>От базовых маршрутов газелей WB до ночного плана</p>
      </div>
      {load.status === "loading" && <p role="status">Загружаем схему города…</p>}
      {load.status === "error" && (
        <div className="city-map-error" role="alert">
          <span>Не удалось загрузить схему города.</span>
          <button
            type="button"
            onClick={() => {
              setLoad({ status: "loading" });
              setAttempt((previous) => previous + 1);
            }}
          >
            Повторить
          </button>
        </div>
      )}
      {load.status === "ready" && <RoutingWorkspace initial={load.workspace} />}
    </section>
  );
}
