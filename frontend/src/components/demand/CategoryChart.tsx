/**
 * Популярность категорий: продажи в городе за 30 дней (GET /catalog/categories).
 * Запрещённые для mesh категории — серым, с причиной запрета в подписи.
 * Клик по столбцу фильтрует таблицу товаров по этой категории.
 */

import { Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { CategoryStats } from "./types";

const ALLOWED = "#257de0";
const BANNED = "#b8b8c2";
const pct = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });

type Props = {
  categories: CategoryStats[];
  selected: string;
  onSelect: (id: string) => void;
};

export default function CategoryChart({ categories, selected, onSelect }: Props) {
  const data = categories.map((c) => ({
    ...c,
    label: c.mesh_allowed ? c.name : `${c.name} — ${c.ban_reason ?? "запрещена"}`,
    tag: `${c.sales_30d} шт. · ${pct.format(c.share_pct)}%`,
  }));
  return (
    <>
      <ResponsiveContainer width="100%" height={36 * data.length + 40}>
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 140, bottom: 4, left: 8 }}>
          <CartesianGrid horizontal={false} stroke="#ececf1" />
          <XAxis type="number" tick={{ fontSize: 12 }} allowDecimals={false} />
          <YAxis type="category" dataKey="label" width={330} tick={{ fontSize: 13 }} />
          <Tooltip
            formatter={(v) => [`${v} шт.`, "Продажи за 30 дней"]}
            cursor={{ fill: "#f3f0fb" }}
          />
          <Bar
            dataKey="sales_30d"
            radius={[0, 4, 4, 0]}
            cursor="pointer"
            onClick={(entry) => {
              const id = (entry as unknown as { payload?: CategoryStats }).payload?.id;
              if (id) onSelect(id === selected ? "" : id);
            }}
          >
            {data.map((c) => (
              <Cell
                key={c.id}
                fill={c.mesh_allowed ? ALLOWED : BANNED}
                fillOpacity={selected && selected !== c.id ? 0.35 : 1}
              />
            ))}
            <LabelList dataKey="tag" position="right" style={{ fontSize: 12, fill: "#555" }} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div className="demand-legend">
        <span><i style={{ background: ALLOWED }} />mesh разрешён</span>
        <span><i style={{ background: BANNED }} />запрещена для mesh — отказы едут в хаб</span>
        <span>Клик по столбцу — фильтр таблицы</span>
      </div>
    </>
  );
}
