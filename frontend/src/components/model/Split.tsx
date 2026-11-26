/**
 * Блок 2. Кому достаётся эффект цепочки: ПВЗ, продавец, маркетплейс.
 * Доли бывают отрицательными (сторона теряет), поэтому столбцы идут от нуля в обе стороны.
 * Строка проверки: сумма долей ровно равна эффекту — деньги не берутся из ниоткуда.
 */

import { money } from "./format";
import type { Distribution } from "./types";

const PARTIES: { key: keyof Distribution; label: string; slot: number }[] = [
  { key: "pvz_kop", label: "ПВЗ", slot: 1 },
  { key: "seller_kop", label: "Продавец", slot: 2 },
  { key: "mp_kop", label: "Маркетплейс", slot: 3 },
];

export default function Split({ effect, split }: { effect: number; split: Distribution }) {
  const sum = split.pvz_kop + split.seller_kop + split.mp_kop;
  const max = Math.max(...PARTIES.map((p) => Math.abs(split[p.key])), 1);
  return (
    <div className="model-split">
      {PARTIES.map((p) => {
        const value = split[p.key];
        const width = `${(Math.abs(value) / max) * 50}%`;
        return (
          <div className="model-split-row" key={p.key}>
            <span>{p.label}</span>
            <div className="model-split-track" aria-hidden="true">
              <span className="model-split-zero" />
              <span
                className="model-split-bar"
                style={{
                  width,
                  background: `var(--series-${p.slot})`,
                  [value >= 0 ? "left" : "right"]: "50%",
                }}
              />
            </div>
            <strong className={value < 0 ? "is-negative" : ""}>
              {value > 0 ? "+" : ""}
              {money(value)}
            </strong>
          </div>
        );
      })}
      <p className={`model-check ${sum === effect ? "is-ok" : "is-bad"}`} role="status">
        {sum === effect ? "✓" : "✗"} Сумма долей {money(sum)} {sum === effect ? "=" : "≠"} эффект
        цепочки {money(effect)}
      </p>
    </div>
  );
}
