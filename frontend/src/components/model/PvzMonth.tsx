/**
 * Блок 4. Месяц работы ПВЗ: доля склада под mesh (10–50%, выбирает ПВЗ) и загрузка
 * полки → доход, затраты, прибыль и безубыточная загрузка из /model/pvz-economics.
 */

import { useEffect, useState } from "react";
import { api } from "../../api";
import { money, percent, plain, toNumber } from "./format";
import type { PvzMonth as Month } from "./types";

export default function PvzMonth() {
  const [share, setShare] = useState(25);
  const [load, setLoad] = useState(50);
  const [data, setData] = useState<Month | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api<Month>(`/model/pvz-economics?share=${share}&utilization=${load / 100}`, "logist", {
        signal: controller.signal,
      })
        .then((d) => !controller.signal.aborted && setData(d))
        .catch(() => undefined);
    }, 200); // не дёргаем API на каждый шаг ползунка
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [share, load]);

  const breakEven = data?.break_even_utilization;
  return (
    <div className="model-pvz">
      <div className="model-sliders">
        <label className="model-field">
          Доля склада под mesh: <strong>{share}%</strong>
          <input type="range" min={10} max={50} step={5} value={share}
            onChange={(e) => setShare(Number(e.target.value))} />
        </label>
        <label className="model-field">
          Загрузка полки: <strong>{load}%</strong>
          <input type="range" min={0} max={100} step={5} value={load}
            onChange={(e) => setLoad(Number(e.target.value))} />
        </label>
      </div>
      {data && (
        <>
          <div className="model-tiles">
            <div className="model-tile">
              <span>Доход в месяц</span>
              <strong>{money(data.income_kop)}</strong>
            </div>
            <div className="model-tile">
              <span>Затраты в месяц</span>
              <strong>{money(data.cost_kop)}</strong>
            </div>
            <div className={`model-tile ${data.profit_kop < 0 ? "is-negative" : "is-positive"}`}>
              <span>Прибыль ПВЗ в месяц</span>
              <strong>
                {data.profit_kop > 0 ? "+" : ""}
                {money(data.profit_kop)}
              </strong>
              <em>{data.profit_kop < 0 ? "▼ убыток" : "▲ в плюсе"}</em>
            </div>
          </div>
          <dl className="model-pvz-lines">
            <dt>Полка</dt>
            <dd>
              {plain(data.shelf_l, 0)} л, занято {plain(data.occupied_l, 0)} л
            </dd>
            <dt>Хранение</dt>
            <dd>
              {money(data.storage_income_kop)} ({data.storage_tariff_kop_l_day} коп. за литр в сутки)
            </dd>
            <dt>Операции</dt>
            <dd>{money(data.operations_income_kop)}</dd>
            <dt>Место / труд / материалы</dt>
            <dd>
              {money(data.space_cost_kop)} / {money(data.labour_cost_kop)} /{" "}
              {money(data.materials_cost_kop)}
            </dd>
            <dt>Безубыточная загрузка</dt>
            <dd>
              {breakEven == null
                ? "нет: каждый литр приносит убыток"
                : toNumber(breakEven) > 1
                  ? `${percent(breakEven)} — недостижима, полка не может быть заполнена больше 100%`
                  : percent(breakEven)}
            </dd>
          </dl>
        </>
      )}
    </div>
  );
}
