/**
 * Блок 1. Себестоимость одной пригодной отказанной единицы: база (хаб) против mesh.
 * Каждый столбец разбит на статьи; по наведению — формула, входы и основание
 * допущения из /model/params. Под столбцами — та же разбивка таблицей.
 */

import { useState } from "react";
import { money, plain, withUnit } from "./format";
import type { CostItem, Parameter, Scheme } from "./types";

/** Обозначения во входах формул → допущение из каталога. */
const SYMBOLS: Record<string, { param?: string; label?: string }> = {
  C_ret: { param: "hub_return_kop" },
  C_hub: { param: "hub_order_kop" },
  w: { param: "wage_kop_hour" },
  "ИТ": { param: "it_kop" },
  "s_место": { param: "space_kop_l_day" },
  "s_труд": { param: "shelf_labour_kop_l_day" },
  "пакет": { param: "pack_kop" },
  "место": { param: "van_space_kop" },
  V: { param: "volume_l" },
  t: { label: "минут работы сотрудника" },
  p: { label: "вероятность продать до вывоза" },
  "α": { label: "доля продаж газелью" },
  "ожидаемые_сутки": { label: "ожидаемые сутки на полке" },
};

type Hover = { scheme: string; item: CostItem } | null;

function InputList({ item, params }: { item: CostItem; params: Map<string, Parameter> }) {
  return (
    <dl className="model-inputs">
      {Object.entries(item.inputs).map(([symbol, value]) => {
        const known = SYMBOLS[symbol];
        const param = known?.param ? params.get(known.param) : undefined;
        return (
          <div key={symbol}>
            <dt>{symbol}</dt>
            <dd>
              {param ? (
                <>
                  {withUnit(value, param.unit)} — {param.label}
                  <span className="model-basis">{param.basis}</span>
                </>
              ) : (
                <>
                  {plain(value)}
                  {known?.label ? ` — ${known.label}` : ""}
                </>
              )}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

function Bar({
  name,
  scheme,
  max,
  onHover,
  active,
}: {
  name: string;
  scheme: Scheme;
  max: number;
  onHover: (h: Hover) => void;
  active: Hover;
}) {
  const visible = scheme.items.filter((i) => i.amount_kop > 0);
  return (
    <div className="model-bar-row">
      <span className="model-bar-name">{name}</span>
      <div className="model-bar-track" style={{ width: `${(scheme.total_kop / max) * 100}%` }}>
        {visible.map((item) => {
          const slot = scheme.items.indexOf(item) + 1;
          const on = active?.scheme === name && active.item === item;
          return (
            <button
              type="button"
              key={item.label}
              className={`model-seg${on ? " is-active" : ""}`}
              style={{ flexGrow: item.amount_kop, background: `var(--series-${slot})` }}
              aria-label={`${item.label}: ${money(item.amount_kop)}`}
              onMouseEnter={() => onHover({ scheme: name, item })}
              onFocus={() => onHover({ scheme: name, item })}
            />
          );
        })}
      </div>
      <strong className="model-bar-total">{money(scheme.total_kop)}</strong>
    </div>
  );
}

export default function CostBars({
  base,
  mesh,
  params,
}: {
  base: Scheme;
  mesh: Scheme;
  params: Map<string, Parameter>;
}) {
  const [hover, setHover] = useState<Hover>(null);
  const max = Math.max(base.total_kop, mesh.total_kop, 1);
  return (
    <div className="model-costs" onMouseLeave={() => setHover(null)}>
      <Bar name="Через хаб" scheme={base} max={max} onHover={setHover} active={hover} />
      <Bar name="Mesh" scheme={mesh} max={max} onHover={setHover} active={hover} />
      <div className="model-tip" aria-live="polite">
        {hover ? (
          <>
            <p>
              <strong>{hover.item.label}</strong> · {hover.scheme} · {money(hover.item.amount_kop)}
            </p>
            <p className="model-formula">{hover.item.formula}</p>
            <InputList item={hover.item} params={params} />
          </>
        ) : (
          <p className="muted">Наведите на часть столбца — покажем формулу, входы и основание.</p>
        )}
      </div>
      <table className="model-table">
        <thead>
          <tr>
            <th>Схема</th>
            <th>Статья</th>
            <th>Формула</th>
            <th className="num">Сумма</th>
          </tr>
        </thead>
        <tbody>
          {(
            [
              ["Через хаб", base],
              ["Mesh", mesh],
            ] as const
          ).flatMap(([name, scheme]) =>
            scheme.items.map((item, k) => (
              <tr key={`${name}-${item.label}`}>
                <td>{k === 0 ? name : ""}</td>
                <td>
                  <span className="model-swatch" style={{ background: `var(--series-${k + 1})` }} />
                  {item.label}
                </td>
                <td className="model-formula">{item.formula}</td>
                <td className="num">{money(item.amount_kop)}</td>
              </tr>
            )),
          )}
        </tbody>
      </table>
    </div>
  );
}
