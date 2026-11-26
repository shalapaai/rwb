/**
 * Принимает отказ по артикулу и оценке оператора.
 * Показывает решение API и сохраняет ключ, если ответ не дошёл.
 */

import { useId, useState } from "react";
import { pvzPath, usePvzMutation, type Refusal, type RefusalBody } from "./pvz-api";

const grades = [
  { grade: "A", label: "Не вскрыт" },
  { grade: "B", label: "Упаковку восстановил" },
  { grade: "C", label: "Не восстановить" },
] as const;

export default function RefusalForm({
  pvzId,
  isOpen,
  knownSkus,
  onAccepted,
}: {
  pvzId: string;
  isOpen: boolean;
  knownSkus: string[];
  onAccepted: (result: Refusal) => void;
}) {
  const listId = useId();
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
            <label>
              Артикул товара
              <input value={sku} list={listId} disabled={locked} autoComplete="off"
                onChange={(event) => { setSku(event.target.value); action.clearFeedback(); }} />
              <datalist id={listId}>
                {knownSkus.map((code) => <option key={code} value={code} />)}
              </datalist>
            </label>
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
      {state.status === "success" && (
        <div className={`pvz-result pvz-result--${state.data.placement}`} role="status">
          <strong>{state.data.placement === "shelf" ? "На полку" : "В хаб"}</strong>
          <p>{state.data.reason}</p>
          <p>Штрихкод <span className="pvz-code">{state.data.barcode}</span></p>
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
