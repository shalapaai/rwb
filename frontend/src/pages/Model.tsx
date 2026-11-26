/**
 * Экран «Модель»: откуда берутся цифры (задача M3). Все числа — из /model/*:
 * себестоимость отказа «хаб vs mesh» по статьям, деление эффекта, граница
 * выгодности, месяц ПВЗ и таблица допущений. Ничего не досчитываем на фронте.
 */

import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import Assumptions from "../components/model/Assumptions";
import BoundaryChart from "../components/model/BoundaryChart";
import CostBars from "../components/model/CostBars";
import { money, percent, plain, toNumber } from "../components/model/format";
import "../components/model/model.css";
import PvzMonth from "../components/model/PvzMonth";
import Split from "../components/model/Split";
import type { Parameter, ParameterBoundary, UnitEconomics } from "../components/model/types";

type Inputs = { grade: "A" | "B"; demand: string; days: string; volume: string };

function useUnitEconomics(inputs: Inputs) {
  const [data, setData] = useState<UnitEconomics | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    const q = new URLSearchParams({
      grade: inputs.grade,
      demand_per_day: inputs.demand,
      shelf_days: inputs.days,
      volume_l: inputs.volume,
    });
    const timer = setTimeout(() => {
      api<UnitEconomics>(`/model/unit-economics?${q}`, "logist", { signal: controller.signal })
        .then((d) => {
          if (!controller.signal.aborted) {
            setData(d);
            setError(false);
          }
        })
        .catch(() => !controller.signal.aborted && setError(true));
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [inputs]);
  return { data, error };
}

export default function Model() {
  const [params, setParams] = useState<Parameter[]>([]);
  const [boundaries, setBoundaries] = useState<ParameterBoundary[]>([]);
  const [inputs, setInputs] = useState<Inputs>({ grade: "A", demand: "1", days: "14", volume: "2" });
  const { data: unit, error } = useUnitEconomics(inputs);
  const byId = useMemo(() => new Map(params.map((p) => [p.id, p])), [params]);

  useEffect(() => {
    api<Parameter[]>("/model/params", "logist").then(setParams).catch(() => undefined);
    api<ParameterBoundary[]>("/model/boundary", "logist").then(setBoundaries).catch(() => undefined);
  }, []);

  const set = (key: keyof Inputs) => (value: string) => setInputs((prev) => ({ ...prev, [key]: value }));

  return (
    <section className="model-page viz-root" aria-labelledby="model-heading">
      <div className="model-heading">
        <h1 id="model-heading">Модель: откуда берутся цифры</h1>
        <p>Одна пригодная отказанная единица: везти обратно в хаб или продать в городе</p>
      </div>

      <div className="model-controls" role="group" aria-label="Условия расчёта">
        <div className="model-toggle" role="group" aria-label="Оценка отказа">
          {(["A", "B"] as const).map((g) => (
            <button key={g} type="button" aria-pressed={inputs.grade === g} onClick={() => set("grade")(g)}>
              {g === "A" ? "A · не вскрыт" : "B · переупакован"}
            </button>
          ))}
        </div>
        <label className="model-field">
          Спрос в городе, шт/сут
          <input type="number" min={0} step={0.05} value={inputs.demand}
            onChange={(e) => set("demand")(e.target.value)} />
        </label>
        <label className="model-field">
          Срок на полке, сут
          <input type="number" min={0} step={1} value={inputs.days}
            onChange={(e) => set("days")(e.target.value)} />
        </label>
        <label className="model-field">
          Объём, л
          <input type="number" min={0.1} step={0.5} value={inputs.volume}
            onChange={(e) => set("volume")(e.target.value)} />
        </label>
      </div>
      {error && <p role="alert">Проверьте условия: расчёт не принял значения.</p>}

      {unit && (
        <div className="model-tiles">
          <div className={`model-tile ${unit.effect_kop < 0 ? "is-negative" : "is-positive"}`}>
            <span>Эффект цепочки на единицу</span>
            <strong>
              {unit.effect_kop > 0 ? "+" : ""}
              {money(unit.effect_kop)}
            </strong>
            <em>{unit.effect_kop >= 0 ? "▲ mesh выгоден" : "▼ выгоднее хаб"}</em>
          </div>
          <div className="model-tile">
            <span>Вероятность продать в городе</span>
            <strong>{percent(unit.sale_probability)}</strong>
            <em>в среднем {plain(unit.expected_days, 1)} сут на полке</em>
          </div>
          <div className="model-tile">
            <span>Срок для покупателя</span>
            <strong>{plain(toNumber(unit.local_delivery_min) / 60, 1)} ч</strong>
            <em>вместо {plain(unit.hub_delivery_min / 60, 0)} ч из хаба</em>
          </div>
        </div>
      )}

      <article className="model-card">
        <h2>1. Себестоимость: через хаб против mesh</h2>
        {unit ? <CostBars base={unit.base} mesh={unit.mesh} params={byId} /> : <p role="status">Считаем…</p>}
      </article>

      <article className="model-card">
        <h2>2. Кому достаётся эффект</h2>
        {unit && <Split effect={unit.effect_kop} split={unit.distribution} />}
      </article>

      <article className="model-card">
        <h2>3. Граница выгодности</h2>
        <BoundaryChart boundaries={boundaries} />
      </article>

      <article className="model-card">
        <h2>4. ПВЗ за месяц</h2>
        <PvzMonth />
      </article>

      <article className="model-card">
        <h2>5. Допущения</h2>
        <Assumptions params={params} />
      </article>
    </section>
  );
}
