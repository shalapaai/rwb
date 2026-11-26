/**
 * Принимает отказ по артикулу и оценке оператора.
 * Показывает решение API и сохраняет ключ, если ответ не дошёл.
 */

import { useState } from "react";
import SkuPicker from "./SkuPicker";
import { pvzPath, usePvzMutation, type Refusal, type RefusalBody, type Stock } from "./pvz-api";

const grades = [
  { grade: "A", label: "Не вскрыт" },
  { grade: "B", label: "Упаковку восстановил" },
  { grade: "C", label: "Не восстановить" },
] as const;

export default function RefusalForm({
  pvzId,
  isOpen,
  products,
  units,
  onAccepted,
}: {
  pvzId: string;
  isOpen: boolean;
  products: Stock["products"];
  units: Stock["units"];
  onAccepted: (result: Refusal) => void;
}) {
  const [sku, setSku] = useState("");
  const [order, setOrder] = useState("");
  const action = usePvzMutation<Refusal>(pvzPath(pvzId, "refusals"), "POST", (result) => {
    setSku("");
    setOrder("");
    onAccepted(result);
  });
  const { state } = action;
  const waiting = state.status === "pending";
  const locked = waiting || (state.status === "error" && state.uncertain);
  const orderId = order.trim() ? Number(order) : null;
  const validOrder = orderId === null || (Number.isSafeInteger(orderId) && orderId > 0);
  const canAccept = isOpen && !locked && Boolean(sku.trim()) && validOrder;
  const queued = units.filter((unit) => unit.status === "awaiting_shelf");

  function accept(grade: RefusalBody["grade"]) {
    if (!canAccept) return;
    const body: RefusalBody = { sku_id: sku.trim(), order_id: orderId, grade };
    action.submit(body);
  }

  return (
    <section className="pvz-card" aria-labelledby="pvz-refusal-title">
      <h2 id="pvz-refusal-title">Приём отказа</h2>
      {isOpen ? (
        <>
          <div className="pvz-fields">
            <SkuPicker value={sku} products={products} disabled={locked}
              onChange={(value) => { setSku(value); action.clearFeedback(); }} />
            <label>
              Номер заказа <span className="pvz-muted">(необязательно)</span>
              <input type="number" min={1} step={1} value={order} disabled={locked}
                aria-invalid={!validOrder}
                onChange={(event) => { setOrder(event.target.value); action.clearFeedback(); }} />
            </label>
          </div>
          {!validOrder && <p className="pvz-field-error">Укажите целый положительный номер заказа.</p>}
          <div className="pvz-grades" role="group" aria-label="Оценка товара">
            {grades.map(({ grade, label }) => (
              <button key={grade} type="button" disabled={!canAccept} onClick={() => accept(grade)}>
                <span className={`pvz-grade pvz-grade--${grade}`}>{grade}</span>
                {label}
              </button>
            ))}
          </div>
        </>
      ) : <p>ПВЗ закрыт. Приём отказов недоступен.</p>}
      {waiting && <p role="status">Принимаем отказ…</p>}
      {state.status === "success" && state.data.placement !== "queue" && (
        <div className={`pvz-result pvz-result--${state.data.placement}`} role="status">
          <strong>{({ shelf: "На полку", hub: "В хаб", return_queue: "Оформить возврат" })[state.data.placement]}</strong>
          <p>{state.data.reason}</p>
          <p>Штрихкод <span className="pvz-code">{state.data.barcode}</span></p>
        </div>
      )}
      {queued.length > 0 && (
        <div className="pvz-result pvz-result--queue" role="status">
          <strong>Ожидает размещения{queued.length > 1 && ` · ${queued.length} шт.`}</strong>
          <p>Разместите товар по заданию, когда освободится место.</p>
          <p>Штрихкод <span className="pvz-code">{queued[0].barcode}</span></p>
        </div>
      )}
      {state.status === "error" && (
        <div className="pvz-error" role="alert">
          <p>{state.message}</p>
          {state.uncertain && <button type="button" onClick={action.retry}>Повторить приём</button>}
        </div>
      )}
    </section>
  );
}
