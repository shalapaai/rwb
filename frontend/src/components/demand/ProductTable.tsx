/**
 * Товары города по спросу (GET /catalog/products): продажи за 30 дней, спрос в сутки
 * против порога модели и решение «можно ли оставить отказ в mesh» с причиной.
 * Строки «не в mesh» подсвечены красным. Все числа — из API, здесь только формат.
 */

import { toNumber } from "../model/format";
import type { ProductRow } from "./types";

const rub = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const per = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });

/** Шкала спроса логарифмическая: спрос от 0,01 до 3 шт./сут, порог ≈ 0,04 — иначе он не виден. */
const LOG_MIN = Math.log10(0.01);
const LOG_MAX = Math.log10(3);
function pos(x: number): number {
  if (x <= 0.01) return 0;
  return Math.min(100, ((Math.log10(x) - LOG_MIN) / (LOG_MAX - LOG_MIN)) * 100);
}

function Meter({ demand, threshold }: { demand: number; threshold: number }) {
  return (
    <div
      className="demand-meter"
      title={`спрос ${per.format(demand)} / порог ${per.format(threshold)} шт. в сутки (лог. шкала)`}
    >
      <i className={demand < threshold ? "low" : ""} style={{ width: `${pos(demand)}%` }} />
      <b style={{ left: `${pos(threshold)}%` }} />
    </div>
  );
}

export default function ProductTable({ rows }: { rows: ProductRow[] }) {
  return (
    <div className="demand-table-wrap">
      <table className="demand-table">
        <thead>
          <tr>
            <th>Товар</th>
            <th>Категория</th>
            <th>Продавец</th>
            <th style={{ textAlign: "right" }}>Цена</th>
            <th style={{ textAlign: "right" }}>Объём</th>
            <th style={{ textAlign: "right" }}>Продаж за 30 дн.</th>
            <th>Спрос, шт./сут</th>
            <th>Mesh</th>
            <th>Причина</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const demand = toNumber(r.demand_per_day);
            const threshold = toNumber(r.threshold_per_day);
            return (
              <tr key={r.sku} className={r.mesh_eligible ? "" : "is-hub"}>
                <td>
                  {r.name}
                  <div className="sku">{r.sku}</div>
                </td>
                <td>{r.category ?? "—"}</td>
                <td>{r.seller_id}</td>
                <td className="num">{rub.format(r.price_kop / 100)} ₽</td>
                <td className="num">{per.format(r.volume_l)} л</td>
                <td className="num">{r.sales_30d}</td>
                <td className="num" style={{ textAlign: "left" }}>
                  {per.format(demand)}
                  <Meter demand={demand} threshold={threshold} />
                </td>
                <td className={`verdict ${r.mesh_eligible ? "ok" : "no"}`}>
                  {r.mesh_eligible ? "✓ полка" : "✗ хаб"}
                </td>
                <td className="reason">{r.reason}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
