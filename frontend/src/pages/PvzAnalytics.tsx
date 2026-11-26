import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { DataTable, LoadError, Metrics } from "../components/analytics/Display";
import { useAnalytics, type Network, type PvzAnalyticsData } from "../components/analytics/data";
import "../components/analytics/analytics.css";
import { money, plain } from "../components/model/format";

const resources: Record<string, string> = {
  labour: "Труд",
  rent: "Место на полке",
  materials: "Материалы",
  shrink: "Потери",
};

function PvzReport({ id }: { id: string }) {
  const { resource, retry } = useAnalytics<PvzAnalyticsData>(
    `/analytics/pvz/${encodeURIComponent(id)}`, "pvz_operator",
  );
  if (resource.status === "loading") return <p role="status">Загружаем аналитику ПВЗ…</p>;
  if (resource.status === "error") return <LoadError message="Не удалось загрузить аналитику ПВЗ." retry={retry} />;
  const data = resource.data;
  const { shelf } = data;

  return (
    <div className="analytics-report">
      <Metrics values={[
        ["Доход mesh", money(data.income_total_kop)],
        ["Затраты mesh", money(data.costs_total_kop)],
        ["Прибыль mesh", money(data.profit_kop)],
        ["Прибыль через хаб", money(data.profit_base_kop)],
        ["Штрафы", money(data.penalties_kop)],
      ]} />
      <div className="analytics-columns">
        <section className="analytics-card">
          <h2>Загрузка mesh-полки</h2>
          <p><strong>{plain(shelf.used_l)} / {plain(shelf.limit_l)} л</strong> · {shelf.load_pct}%</p>
          <p>Товаров: {shelf.units} · Доля склада: {plain(shelf.mesh_share_pct)}%</p>
          <meter aria-label="Загрузка mesh-полки" min={0} max={100} value={shelf.load_pct} />
        </section>
        <section className="analytics-card">
          <h2>Операции за день</h2>
          <p><strong>{data.ops_today} / {data.ops_limit}</strong></p>
          {data.ops_limit > 0 && (
            <meter aria-label="Использование дневного лимита" min={0} max={data.ops_limit} value={data.ops_today} />
          )}
        </section>
      </div>
      <section className="analytics-card">
        <h2>Доход по операциям</h2>
        {data.income.length ? (
          <>
            <div className="analytics-chart">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data.income} layout="vertical" margin={{ right: 18 }} accessibilityLayer>
                  <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                  <XAxis type="number" tickFormatter={(value: number) => money(value)} />
                  <YAxis type="category" dataKey="label" width={130} tick={{ fontSize: 12 }} />
                  <Tooltip formatter={(value) => typeof value === "number" ? money(value) : "—"} />
                  <Bar dataKey="amount_kop" name="Доход" fill="#6b2bd9" />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <DataTable label="Доход по операциям" columns={["Операция", "Сумма"]}>
              {data.income.map((item) => (
                <tr key={item.op}><td>{item.label}</td><td className="analytics-money">{money(item.amount_kop)}</td></tr>
              ))}
            </DataTable>
          </>
        ) : <p className="muted">Поступлений по операциям пока нет.</p>}
      </section>
      <section className="analytics-card">
        <h2>Затраты по статьям</h2>
        {data.costs.length ? (
          <DataTable label="Затраты по статьям" columns={["Статья", "Сумма"]}>
            {data.costs.map((item) => (
              <tr key={item.resource}>
                <td>{resources[item.resource] ?? item.resource}</td>
                <td className="analytics-money">{money(item.amount_kop)}</td>
              </tr>
            ))}
          </DataTable>
        ) : <p className="muted">Затрат пока нет.</p>}
      </section>
    </div>
  );
}

/** Аналитика ПВЗ по проводкам и текущему состоянию полки. */
export default function PvzAnalytics() {
  const { resource, retry } = useAnalytics<Network>("/network", "pvz_operator");
  const [selected, setSelected] = useState("");
  const points = resource.status === "ready" ? resource.data.nodes.filter((node) => node.kind === "pvz") : [];
  const point = points.find((node) => node.id === selected) ?? points[0];

  return (
    <section className="analytics-page" aria-labelledby="pvz-analytics-title">
      <div className="analytics-heading">
        <h1 id="pvz-analytics-title">Аналитика ПВЗ</h1>
        {point && (
          <label>
            Пункт выдачи
            <select value={point.id} onChange={(event) => setSelected(event.target.value)}>
              {points.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.name || option.id}{option.is_open ? "" : " · закрыт"}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {resource.status === "loading" && <p role="status">Загружаем список ПВЗ…</p>}
      {resource.status === "error" && <LoadError message="Не удалось загрузить список ПВЗ." retry={retry} />}
      {resource.status === "ready" && !point && <p>В сети пока нет ПВЗ.</p>}
      {point && <PvzReport key={point.id} id={point.id} />}
    </section>
  );
}
