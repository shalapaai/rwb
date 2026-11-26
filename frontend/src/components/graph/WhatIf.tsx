/**
 * Примеряет доставку товара с выбранной mesh-полки.
 * Запрашивает расчёт, сравнивает варианты и подсвечивает путь на карте.
 */

import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../../api";
import type { components } from "../../api-types";
import CityMap from "./CityMap";
import { formatMoney, legNodes, traceStyles, type City, type Leg, type MapTrace, type Plan } from "./plan-graph";

type Decision = components["schemas"]["Decision"];
type Variant = components["schemas"]["Variant"];
type Quote = {
  variant: Variant;
  source_node: string;
  reason: string;
  total_money_kop: number | null;
  hours: number | null;
  saving_kop: number | null;
  legs: Leg[];
};
type WhatIfResult = {
  forced: Quote;
  best: Decision & { total_money_kop: number | null; saving_kop: number | null };
  hub: Quote;
};
type Preview = { dest: string; data: WhatIfResult };
const formatHours = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });

function WhatIfForm({
  city,
  source,
  onPreview,
}: {
  city: City;
  source: string;
  onPreview: (result: Preview | null) => void;
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
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const pending = useRef<AbortController | null>(null);

  useEffect(() => () => pending.current?.abort(), []);

  function clearPreview() {
    pending.current?.abort();
    setStatus("idle");
    onPreview(null);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const quantity = Number(qty);
    if (!sku || !dest || !Number.isSafeInteger(quantity) || quantity < 1) return;
    clearPreview();
    const controller = new AbortController();
    pending.current = controller;
    setStatus("loading");
    try {
      const data = await api<WhatIfResult>("/routing/what-if", "logist", {
        method: "POST",
        body: JSON.stringify({ source_pvz: source, sku_id: sku, dest_pvz: dest, qty: quantity }),
        signal: controller.signal,
      });
      if (!controller.signal.aborted) {
        onPreview({ dest, data });
        setStatus("idle");
      }
    } catch {
      if (!controller.signal.aborted) setStatus("error");
    }
  }

  return (
    <>
      <p>Источник <strong>{source}</strong></p>
      {shelf.size ? (
        <form className="what-if-form" onSubmit={submit}>
          <label>
            Товар на mesh-полке
            <select value={sku} onChange={(event) => {
              setSku(event.target.value);
              setQty("1");
              clearPreview();
            }}>
              {[...shelf].map(([id, available]) => (
                <option key={id} value={id}>{id} · {available} шт.</option>
              ))}
            </select>
          </label>
          <div className="what-if-form-row">
            <label>
              Номер ПВЗ выдачи
              <select required value={dest} onChange={(event) => {
                setDest(event.target.value);
                clearPreview();
              }}>
                <option value="">Выберите ПВЗ</option>
                {city.nodes.filter((node) => node.kind === "pvz").map(({ id }) => (
                  <option key={id} value={id}>{id}</option>
                ))}
              </select>
            </label>
            <label>
              Штук
              <input type="number" min={1} step={1} max={shelf.get(sku)} required value={qty}
                onChange={(event) => { setQty(event.target.value); clearPreview(); }} />
            </label>
          </div>
          <button type="submit" disabled={status === "loading" || !dest}>
            {status === "loading" ? "Считаем…" : "Рассчитать маршрут"}
          </button>
          {status === "error" && <p role="alert">Не удалось рассчитать маршрут. Повторите запрос.</p>}
        </form>
      ) : <p className="what-if-hint">На mesh-полке этого ПВЗ нет товаров.</p>}
    </>
  );
}

function Metrics({ money, hours, saving }: {
  money: number | null;
  hours: number | null;
  saving?: number | null;
}) {
  if (money === null || hours === null) return null;
  return (
    <dl className="what-if-metrics">
      <div><dt>Стоимость</dt><dd>{formatMoney(money)}</dd></div>
      <div><dt>Срок</dt><dd>{formatHours.format(hours)} ч</dd></div>
      {saving != null && <div><dt>Выгода против хаба</dt><dd>{formatMoney(saving)}</dd></div>}
    </dl>
  );
}

function WhatIfResults({ data }: { data: WhatIfResult }) {
  const { forced, best, hub } = data;
  const chosen = best.assignment;
  return (
    <div className="what-if-results" aria-live="polite">
      <section className="what-if-choice what-if-choice--primary">
        <h3>Из выбранного ПВЗ</h3>
        <strong>{forced.total_money_kop === null ? "Доставка недоступна" : traceStyles[forced.variant].label}</strong>
        <p>{forced.reason}</p>
        <Metrics money={forced.total_money_kop} hours={forced.hours} saving={forced.saving_kop} />
      </section>
      <section className="what-if-choice">
        <h3>Базовый вариант через хаб</h3>
        <Metrics money={hub.total_money_kop} hours={hub.hours} />
      </section>
      <section className="what-if-choice">
        <h3>Выбор алгоритма</h3>
        {chosen ? (
          <>
            <strong>{traceStyles[chosen.variant].label}</strong>
            <p>Источник {chosen.source_node === "HUB" ? "хаб" : chosen.source_node}</p>
            <Metrics money={best.total_money_kop} hours={chosen.hours} saving={best.saving_kop} />
          </>
        ) : <p>Нет доступного варианта.</p>}
        <details className="what-if-candidates">
          <summary>Кандидаты и причины</summary>
          <ul className="night-plan-data">
            {best.candidates.map((candidate, index) => (
              <li key={index}>
                <strong>{traceStyles[candidate.variant].label}</strong>
                {candidate.chosen && " · выбран"}
                <span>{candidate.reason}</span>
              </li>
            ))}
          </ul>
        </details>
      </section>
    </div>
  );
}

function previewTraces(preview: Preview | null, routes: Plan["routes"]): MapTrace[] {
  if (!preview || preview.data.forced.total_money_kop === null) return [];
  const { dest, data: { forced } } = preview;
  const { variant, source_node: source, legs, reason } = forced;
  if (legs.length) {
    const plannedRoutes = new Map(routes.map((route) => [route.id, route]));
    return legs.map((leg, index) => ({
      id: `what-if-${index}`, kind: variant, label: reason, selected: true,
      nodes: legNodes(leg, plannedRoutes),
    }));
  }
  return [{
    id: "what-if", kind: variant, label: reason, selected: true,
    nodes: variant === "hub" ? [source, dest] : [source],
  }];
}

export default function WhatIf({
  city, plan, source, onSelectPvz,
}: {
  city: City;
  plan: Plan;
  source: string | null;
  onSelectPvz: (id: string) => void;
}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const traces = previewTraces(preview, plan.routes);

  return (
    <div className="what-if-view">
      <aside className="night-plan-panel what-if-panel" aria-label="Куда нужно товару">
        <h2>Маршрут товара</h2>
        {source ? (
          <WhatIfForm key={source} city={city} source={source} onPreview={setPreview} />
        ) : <p>Выберите ПВЗ-источник на схеме.</p>}
        {preview && <WhatIfResults data={preview.data} />}
      </aside>
      <CityMap
        city={city}
        traces={traces}
        legend={[...new Set(traces.map((trace) => trace.kind))]}
        selectedPvz={source}
        onSelectPvz={(pvz) => {
          if (pvz !== source) {
            setPreview(null);
            onSelectPvz(pvz);
          }
        }}
      />
    </div>
  );
}
