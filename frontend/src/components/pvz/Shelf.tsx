/**
 * Показывает занятые литры, долю склада и единицы на mesh-полке.
 * Лимит, статусы и возраст единиц приходят с сервера.
 */

import { useEffect, useState } from "react";
import { pvzPath, usePvzMutation, type Settings, type Stock } from "./pvz-api";

const volume = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const stockSections = [
  { id: "shelf", label: "На полке", empty: "На полке нет товаров в этом разделе." },
  { id: "waiting", label: "Ожидают размещения", empty: "Нет товаров, ожидающих размещения." },
  { id: "returns", label: "К возврату", empty: "Нет товаров к возврату." },
] as const;
type StockSection = (typeof stockSections)[number]["id"];

const statuses: Record<string, { label: string; section: StockSection }> = {
  on_shelf: { label: "На полке", section: "shelf" },
  reserved: { label: "В резерве", section: "shelf" },
  awaiting_return: { label: "Оформить возврат", section: "returns" },
  remove_pending: { label: "Убрать с полки", section: "returns" },
  reduction_planned: { label: "Возврат по плану", section: "returns" },
  awaiting_shelf: { label: "Ожидает размещения", section: "waiting" },
};

export function ShelfSettings({
  stock,
  refreshing,
  onSaved,
  onSavingChange,
}: {
  stock: Stock;
  refreshing: boolean;
  onSaved: () => void;
  onSavingChange: (saving: boolean) => void;
}) {
  const [draft, setDraft] = useState<number | null>(null);
  const action = usePvzMutation<Settings>(pvzPath(stock.pvz_id, "settings"), "PATCH", () => {
    setDraft(null);
    onSaved();
  });
  const { state } = action;
  const share = draft ?? stock.pending_share_pct ?? stock.mesh_share_pct;
  const saving = state.status === "pending";
  const locked = saving || (state.status === "error" && state.uncertain);
  useEffect(() => onSavingChange(locked), [locked, onSavingChange]);
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
      {full && stock.pending_share_pct == null && <p className="pvz-shelf-warning">Полка заполнена. Новые пригодные отказы попадут в очередь.</p>}
      {stock.pending_share_pct != null && (
        <p className="pvz-shelf-warning" role="status">
          Сейчас {volume.format(stock.mesh_share_pct)}%, цель {stock.pending_share_pct}%.{" "}
          {stock.estimated_days == null
            ? "Срок уточнится, когда освободятся товары для возврата."
            : `Ориентировочный срок — ${stock.estimated_days} дн. Следующий шаг — по кнопке «Следующий день».`}
        </p>
      )}
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
          disabled={locked || refreshing ||
              (share === stock.mesh_share_pct && stock.pending_share_pct == null) ||
              share === stock.pending_share_pct}
          onClick={() => action.submit({ mesh_share_pct: share })}>
          {saving ? "Сохраняем…" : "Сохранить долю"}
        </button>
        {state.status === "success" && !state.data.pending_share_pct && <span className="pvz-saved" role="status">Доля сохранена</span>}
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
  const [selected, setSelected] = useState<StockSection>("shelf");
  const grouped: Record<StockSection, Stock["units"]> = { shelf: [], waiting: [], returns: [] };
  for (const unit of stock.units) {
    grouped[statuses[unit.status]?.section ?? "shelf"].push(unit);
  }
  const visible = grouped[selected];

  return (
    <section className="pvz-card" aria-labelledby="pvz-stock-title">
      <h2 id="pvz-stock-title">Товары на полке и в очереди <span className="pvz-count">{stock.units.length}</span></h2>
      <div className="pvz-stock-filters" role="group" aria-label="Фильтр по статусу">
        {stockSections.map(({ id, label }) => (
          <button key={id} type="button" aria-pressed={selected === id}
            className={selected === id ? "pvz-primary" : undefined} onClick={() => setSelected(id)}>
            {label} <span>{grouped[id].length}</span>
          </button>
        ))}
      </div>
      <div key={selected} className="pvz-stock-scroll" role="region" aria-label="Товары выбранного раздела" tabIndex={0}>
        {visible.length ? (
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
              {visible.map((unit) => {
                const age = unit.age_days >= 30 ? "late" : unit.age_days >= 14 ? "warning" : "normal";
                return (
                  <tr key={unit.unit_id} data-recent={unit.unit_id === recentUnit || undefined}>
                    <td>{unit.sku}</td>
                    <td className="pvz-code">{unit.barcode}</td>
                    <td>{unit.grade ?? "—"}</td>
                    <td><span className={`pvz-status--${unit.status}`}>{statuses[unit.status]?.label ?? unit.status}</span></td>
                    <td>{volume.format(unit.volume_l)} л</td>
                    <td>{unit.shelved_at ? <span className={`pvz-age pvz-age--${age}`}>{unit.age_days} дн.</span> : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : <p className="pvz-muted">{stockSections.find(({ id }) => id === selected)?.empty}</p>}
      </div>
    </section>
  );
}

export function TodayWriteOffs({ items }: { items: Stock["writeoffs_today"] }) {
  return (
    <details className="pvz-card pvz-writeoffs">
      <summary>
        <h2 id="pvz-writeoffs-title">Списано сегодня <span className="pvz-count">{items.length}</span></h2>
      </summary>
      {items.length > 0 ? (
        <div className="pvz-table-scroll" role="region" aria-labelledby="pvz-writeoffs-title" tabIndex={0}>
          <table className="pvz-table">
            <thead><tr>
              <th scope="col">Товар</th>
              <th scope="col">Штрихкод</th>
              <th scope="col">Объём</th>
              <th scope="col">Причина списания</th>
            </tr></thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.unit_id}>
                  <td>{item.sku}{item.name && item.name !== item.sku && <div className="pvz-muted">{item.name}</div>}</td>
                  <td className="pvz-code">{item.barcode}</td>
                  <td>{volume.format(item.volume_l)} л</td>
                  <td>{item.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="pvz-muted">Сегодня списаний по упаковке или объёму нет.</p>}
    </details>
  );
}
