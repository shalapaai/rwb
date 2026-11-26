/**
 * Показывает действия по заказам и задания на снятие и размещение товаров.
 * Версия берётся из задания, а после действия перечитываются задания и полка.
 */

import { Fragment, type ReactNode, useState } from "react";
import type { components } from "../../api-types";
import { pvzPath, usePvzMutation, type usePvzResource } from "./pvz-api";

const dateFormat = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });

export type TaskQueues = components["schemas"]["TasksOut"];

const steps = [
  { queue: "to_pick", action: "pick", label: "Отобрать", done: "Товар отобран" },
  { queue: "to_load", action: "load", label: "Погрузить", done: "Товар погружен" },
  { queue: "to_receive", action: "receive", label: "Принять", done: "Товар принят" },
  { queue: "to_issue", action: "issue", label: "Выдать", done: "Товар выдан" },
] as const;

type Feedback = { text: string; conflict?: boolean };

function TaskAction({ title, detail, label, done, path, version, refreshing, onFeedback }: {
  title: string;
  detail: ReactNode;
  label: string;
  done: string;
  path: string;
  version: number;
  refreshing: boolean;
  onFeedback: (feedback: Feedback) => void;
}) {
  const action = usePvzMutation<unknown>(
    path,
    "POST",
    () => onFeedback({ text: done }),
    (message) => onFeedback({ text: message, conflict: true }),
  );
  const { state } = action;
  const pending = state.status === "pending";
  const uncertain = state.status === "error" && state.uncertain;
  const alreadyHandled = state.status === "success" || (state.status === "error" && state.code === 409);
  return (
    <li className="pvz-task">
      <div>
        <strong>{title}</strong>
        <p className="pvz-muted">{detail}</p>
      </div>
      <button type="button" className="pvz-primary"
        aria-label={`${label} · ${title}`}
        disabled={refreshing || pending || alreadyHandled || uncertain}
        onClick={() => action.submit({ version })}>
        {pending ? "Выполняем…" : label}
      </button>
      {state.status === "error" && state.code !== 409 && (
        <div className="pvz-error" role="alert">
          <p>{state.message}</p>
          {uncertain && <button type="button" onClick={action.retry}>Повторить действие</button>}
        </div>
      )}
    </li>
  );
}

export default function Tasks({ tasks, onChanged }: {
  tasks: ReturnType<typeof usePvzResource<TaskQueues>>;
  onChanged: () => void;
}) {
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const queues = tasks.data;
  const items = queues ? steps.flatMap((step) => queues[step.queue].map((task) => ({ task, step }))) : [];
  const scrollable = (queues?.shelf.length ?? 0) + items.length > 3;

  function onFeedback(result: Feedback) {
    setFeedback(result);
    tasks.reload();
    onChanged();
  }

  return (
    <section className="pvz-card" aria-labelledby="pvz-tasks-title" aria-busy={tasks.loading}>
      <div className="pvz-subheading">
        <h2 id="pvz-tasks-title">Задания</h2>
        <button type="button" disabled={tasks.loading} onClick={() => {
          setFeedback(null);
          tasks.reload();
          onChanged();
        }}>Обновить задания</button>
      </div>
      {feedback && (
        <p role={feedback.conflict ? "alert" : "status"} className={feedback.conflict ? "pvz-field-error" : "pvz-saved"}>
          {feedback.text}
        </p>
      )}
      {queues?.reduction && (
        <p>Возвраты при сокращении полки. Сегодня отправлено в хаб{" "}
          {queues.reduction.sent_l} из {queues.reduction.limit_l} л.</p>
      )}
      {tasks.error && <p role="alert" className="pvz-field-error">{tasks.error}</p>}
      {!queues && tasks.loading && <p role="status">Загружаем задания…</p>}
      {queues && !queues.shelf.length && !items.length && <p className="pvz-muted">Нет активных заданий.</p>}
      {queues && (queues.shelf.length > 0 || items.length > 0) && (
        <ul className={`pvz-task-list${scrollable ? " pvz-task-list--scroll" : ""}`}
          aria-labelledby="pvz-tasks-title" tabIndex={scrollable ? 0 : undefined}>
          {queues.shelf.map((task, index) => {
            const shelve = task.action === "shelve";
            const label = { remove: "Снять для возврата", return: "Оформить возврат", reduce: "Отправить в хаб", shelve: "Поставить на полку" }[task.action];
            const previous = queues.shelf[index - 1];
            const newDay = task.action === "reduce" &&
              (previous?.action !== "reduce" || previous.scheduled_for !== task.scheduled_for);
            return (
              <Fragment key={`unit-${task.unit_id}-${task.action}-${task.version}`}>
                {newDay && <li className="pvz-task-day">{task.scheduled_for
                  ? dateFormat.format(new Date(task.scheduled_for)) : "Без даты отправки"}</li>}
                <TaskAction
                  title={`${shelve ? "Размещение на полке" : task.action === "reduce" ? "Сокращение полки" : "Возврат в хаб"} · ${task.sku}`}
                  detail={<><span className="pvz-code">{task.barcode}</span> · {task.volume_l} л
                    {task.blocked_reason && <> · {task.blocked_reason}</>}</>}
                  label={label}
                  done={`${task.barcode} · ${shelve ? "размещён на полке" : "оформлен возврат в хаб"}`}
                  path={pvzPath(queues.pvz_id, `units/${task.unit_id}/${task.action}`)}
                  version={task.version} refreshing={tasks.loading || Boolean(task.blocked_reason)} onFeedback={onFeedback} />
              </Fragment>
            );
          })}
          {items.map(({ task, step }) => {
            const source = task.source_node === "HUB" ? "Хаб" : task.source_node;
            const pickup = task.variant === "neighbor" && source ? source : task.pvz_id;
            const route = source && source !== pickup ? `${source} → ${pickup}` : pickup;
            return (
              <TaskAction key={`${task.order_id}-${step.action}-${task.version}`}
                title={`Заказ №${task.order_id}`} label={step.label}
                detail={<>{task.sku} · {task.qty} шт. · {route}{task.van && ` · ${task.van}`}
                  {task.variant === "hub" && <> <span className="stub-badge">ИМИТАЦИЯ</span></>}</>}
                done={`${step.done} · заказ №${task.order_id}`}
                path={`/orders/${task.order_id}/${step.action}`} version={task.version}
                refreshing={tasks.loading} onFeedback={onFeedback} />
            );
          })}
        </ul>
      )}
    </section>
  );
}
