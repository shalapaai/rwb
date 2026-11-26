/**
 * Показывает занятые литры, долю склада и единицы на mesh-полке.
 * Лимит, статусы и возраст единиц приходят с сервера.
 */

import { useState } from "react";
import { pvzPath, usePvzMutation, type Settings, type Stock } from "./pvz-api";

const volume = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const statusLabels: Record<string, string> = {
  on_shelf: "На полке",
  reserved: "В резерве",
};

export function ShelfSettings({
  stock,
  refreshing,
  onSaved,
}: {
  stock: Stock;
  refreshing: boolean;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<number | null>(null);
  const action = usePvzMutation<Settings>(pvzPath(stock.pvz_id, "settings"), "PATCH", () => {
    setDraft(null);
    onSaved();
  });
  const { state } = action;
  const share = draft ?? stock.mesh_share_pct;
  const saving = state.status === "pending";
  const locked = saving || (state.status === "error" && state.uncertain);
  const usage = `${volume.format(stock.used_l)} / ${volume.format(stock.limit_l)} л`;
  const full = stock.used_l >= stock.limit_l;

  return (
    <section className="pvz-card" aria-labelledby="pvz-shelf-title" aria-busy={refreshing}>
      <h2 id="pvz-shelf-title">Mesh-полка</h2>
      <div className="pvz-usage">
        <strong>{usage}</strong>
        <span className="pvz-muted">занято / лимит</span>
      </div>
      <progress className={`pvz-progress${full ? " pvz-progress--full" : ""}`}
        value={Math.min(stock.used_l, stock.limit_l)} max={stock.limit_l || 1}
        aria-label="Занятость mesh-полки" aria-valuetext={usage} />
      {full && <p className="pvz-shelf-warning">На полке нет места для новых отказов.</p>}
      <div className="pvz-share">
        <label htmlFor="pvz-mesh-share">Доля склада под mesh</label>
        <output htmlFor="pvz-mesh-share">{share}%</output>
        <input id="pvz-mesh-share" type="range" min={10} max={50} step={1} value={share}
          disabled={locked}
          onChange={(event) => { setDraft(Number(event.target.value)); action.clearFeedback(); }} />
        <div className="pvz-share-bounds"><span>10%</span><span>50%</span></div>
      </div>
      <div className="pvz-card-actions">
        <button type="button" className="pvz-primary"
          disabled={locked || refreshing || share === stock.mesh_share_pct}
          onClick={() => action.submit({ mesh_share_pct: share })}>
          {saving ? "Сохраняем…" : "Сохранить долю"}
        </button>
        {state.status === "success" && <span className="pvz-saved" role="status">Доля сохранена</span>}
      </div>
      {state.status === "error" && (
        <div className="pvz-error" role="alert">
          <p>{state.message}</p>
          {state.uncertain && <button type="button" onClick={action.retry}>Повторить сохранение</button>}
        </div>
      )}
    </section>
  );
}

export function ShelfUnits({ stock, recentUnit }: { stock: Stock; recentUnit: number | null }) {
  return (
    <section className="pvz-card" aria-labelledby="pvz-stock-title">
      <h2 id="pvz-stock-title">Единицы на полке <span className="pvz-count">{stock.units.length}</span></h2>
      {stock.units.length ? (
        <div className="pvz-table-scroll" role="region" aria-label="Единицы на полке" tabIndex={0}>
          <table className="pvz-table">
            <thead>
              <tr>
                <th scope="col">Артикул</th>
                <th scope="col">Штрихкод</th>
                <th scope="col">Оценка</th>
                <th scope="col">Статус</th>
                <th scope="col">Объём</th>
                <th scope="col">Возраст</th>
              </tr>
            </thead>
            <tbody>
              {stock.units.map((unit) => {
                const age = unit.age_days >= 30 ? "late" : unit.age_days >= 14 ? "warning" : "normal";
                return (
                  <tr key={unit.unit_id} data-recent={unit.unit_id === recentUnit || undefined}>
                    <td>{unit.sku}</td>
                    <td className="pvz-code">{unit.barcode}</td>
                    <td>{unit.grade ?? "—"}</td>
                    <td>{statusLabels[unit.status] ?? unit.status}</td>
                    <td>{volume.format(unit.volume_l)} л</td>
                    <td><span className={`pvz-age pvz-age--${age}`}>{unit.age_days} дн.</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : <p className="pvz-muted">Полка пуста.</p>}
    </section>
  );
}
