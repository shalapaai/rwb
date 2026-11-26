/**
 * Блок 3. Граница выгодности: эффект цепочки на одну единицу при изменении одного
 * параметра (остальные — базовые). Линия нуля и зоны «mesh выгоден» / «в хаб»
 * строятся по признаку mesh_profitable каждой точки; правило — из /model/boundary.
 */

import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../../api";
import { money, plain, toNumber } from "./format";
import type { ParameterBoundary, Sensitivity } from "./types";

const PARAMS: { id: string; label: string }[] = [
  { id: "demand_per_day", label: "Спрос в городе" },
  { id: "shelf_days", label: "Срок на полке до вывоза" },
  { id: "volume_l", label: "Объём единицы" },
  { id: "opened_share", label: "Доля вскрытых (B)" },
  { id: "repack_cost_kop", label: "Стоимость переупаковки" },
  { id: "storage_tariff_kop_l_day", label: "Тариф хранения" },
  { id: "loss_share", label: "Потери" },
  { id: "season_factor", label: "Сезонный коэффициент" },
];

type Point = { x: number; effect: number; ok: boolean };

/** Непрерывные участки, где mesh выгоден / невыгоден, — для подложки зон.
 *  Граница — там, где линия пересекает ноль (интерполяция между соседними точками). */
function zones(points: Point[]): { from: number; to: number; ok: boolean }[] {
  const out: { from: number; to: number; ok: boolean }[] = [];
  points.forEach((p, i) => {
    const last = out[out.length - 1];
    if (!last) {
      out.push({ from: p.x, to: p.x, ok: p.ok });
    } else if (last.ok === p.ok) {
      last.to = p.x;
    } else {
      const prev = points[i - 1];
      const cross =
        prev.effect !== p.effect
          ? prev.x + ((0 - prev.effect) * (p.x - prev.x)) / (p.effect - prev.effect)
          : (prev.x + p.x) / 2;
      last.to = cross;
      out.push({ from: cross, to: p.x, ok: p.ok });
    }
  });
  return out;
}

export default function BoundaryChart({ boundaries }: { boundaries: ParameterBoundary[] }) {
  const [param, setParam] = useState("demand_per_day");
  const [data, setData] = useState<Sensitivity | null>(null);
  const [error, setError] = useState(false);

  // кривую строим в том же диапазоне, где /model/boundary искал границу, — чтобы переход был виден
  const range = boundaries.find((b) => b.param === param);
  const from = range?.search_from;
  const to = range?.search_to;

  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    const q = new URLSearchParams({ param });
    if (from !== undefined && to !== undefined) {
      q.set("from", String(from));
      q.set("to", String(to));
    }
    api<Sensitivity>(`/model/sensitivity?${q}`, "logist", { signal: controller.signal })
      .then((d) => !controller.signal.aborted && setData(d))
      .catch(() => !controller.signal.aborted && setError(true));
    return () => controller.abort();
  }, [param, from, to]);

  const kopAxis = data?.unit.startsWith("коп") ?? false; // копейки по оси X показываем в рублях
  const unit = data ? (kopAxis ? data.unit.replace(/^коп/, "₽") : data.unit) : "";
  const points: Point[] = (data?.points ?? []).map((p) => ({
    x: toNumber(p.x) / (kopAxis ? 100 : 1),
    effect: p.effect_kop / 100,
    ok: p.mesh_profitable,
  }));
  const rule = boundaries.find((b) => b.param === param)?.chain;
  const base = data ? toNumber(data.base_value) / (kopAxis ? 100 : 1) : null;
  const span = points.length ? points[points.length - 1].x - points[0].x : 0;

  return (
    <div className="model-boundary">
      <label className="model-field">
        Параметр
        <select value={param} onChange={(e) => setParam(e.target.value)}>
          {PARAMS.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
      </label>
      {rule && <p className="model-rule">{rule.rule}</p>}
      {error && <p role="alert">Не удалось загрузить расчёт.</p>}
      <div className="model-chart" role="img" aria-label="Эффект цепочки на единицу в рублях">
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={points} margin={{ top: 12, right: 16, bottom: 24, left: 8 }}>
            {zones(points).map((z) => (
              <ReferenceArea
                key={`${z.from}-${z.ok}`}
                x1={z.from}
                x2={z.to}
                fill={z.ok ? "var(--zone-mesh)" : "var(--zone-hub)"}
                fillOpacity={1}
                ifOverflow="hidden"
                // узкую зону не подписываем — подпись налезет на ось; правило написано над графиком
                label={
                  z.to - z.from >= span * 0.1
                    ? {
                        value: z.ok ? "mesh выгоден" : "в хаб",
                        position: "insideTop",
                        fill: "var(--text-secondary)",
                        fontSize: 12,
                      }
                    : undefined
                }
              />
            ))}
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis
              dataKey="x"
              type="number"
              domain={["dataMin", "dataMax"]}
              tick={{ fill: "var(--text-secondary)", fontSize: 12 }}
              tickFormatter={(v: number) => plain(v, 2)}
              label={{ value: unit, position: "insideBottom", offset: -14, fontSize: 12 }}
            />
            <YAxis
              tick={{ fill: "var(--text-secondary)", fontSize: 12 }}
              tickFormatter={(v: number) => `${plain(v, 0)} ₽`}
              width={64}
            />
            <ReferenceLine y={0} stroke="var(--text-primary)" strokeWidth={1} />
            {base !== null && (
              <ReferenceLine
                x={base}
                stroke="var(--text-secondary)"
                strokeDasharray="4 4"
                label={{
                  value: "базовое значение",
                  // подпись — с той стороны линии, где больше места
                  position: base > points[0]?.x + span / 2 ? "insideBottomRight" : "insideBottomLeft",
                  fontSize: 11,
                  fill: "var(--text-secondary)",
                }}
              />
            )}
            <Tooltip
              cursor={{ stroke: "var(--text-secondary)", strokeWidth: 1 }}
              content={({ active, payload }) => {
                const p = active && payload?.length ? (payload[0].payload as Point) : null;
                if (!p) return null;
                return (
                  <div className="model-chart-tip">
                    <div>
                      {plain(p.x, 3)} {unit}
                    </div>
                    <strong>{money(Math.round(p.effect * 100))}</strong> на единицу ·{" "}
                    {p.ok ? "mesh выгоден" : "в хаб"}
                  </div>
                );
              }}
            />
            <Line
              type="monotone"
              dataKey="effect"
              stroke="var(--series-1)"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 5, stroke: "var(--surface-1)", strokeWidth: 2 }}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
