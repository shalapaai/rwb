/**
 * Отображает очереди M1 и выполняет текущий шаг заказа.
 * Версия берётся из задания, а после действия перечитываются задания и полка.
 */

import { useState } from "react";
import { usePvzMutation, type usePvzResource } from "./pvz-api";

type Task = {
  order_id: number;
  version: number;
  sku: string;
  qty: number;
  source_node: string | null;
  pvz_id: string;
  van: string | null;
  variant: string | null;
};
export type TaskQueues = Record<"to_pick" | "to_load" | "to_receive" | "to_issue", Task[]>;

const steps = [
  { queue: "to_pick", action: "pick", label: "Отобрать", done: "Товар отобран" },
  { queue: "to_load", action: "load", label: "Погрузить", done: "Товар погружен" },
  { queue: "to_receive", action: "receive", label: "Принять", done: "Товар принят" },
  { queue: "to_issue", action: "issue", label: "Выдать", done: "Товар выдан" },
] as const;

type Feedback = { text: string; conflict?: boolean };

function TaskAction({ task, step, refreshing, onFeedback }: {
  task: Task;
  step: typeof steps[number];
  refreshing: boolean;
  onFeedback: (feedback: Feedback) => void;
}) {
  const action = usePvzMutation<unknown>(
    `/orders/${task.order_id}/${step.action}`,
    "POST",
    () => onFeedback({ text: `${step.done} · заказ №${task.order_id}` }),
    (message) => onFeedback({ text: message, conflict: true }),
  );
  const { state } = action;
  const pending = state.status === "pending";
  const uncertain = state.status === "error" && state.uncertain;
  const alreadyHandled = state.status === "success" || (state.status === "error" && state.code === 409);
  const source = task.source_node === "HUB" ? "Хаб" : task.source_node;
  const pickup = task.variant === "neighbor" && source ? source : task.pvz_id;
  const route = source && source !== pickup ? `${source} → ${pickup}` : pickup;

  return (
    <li className="pvz-task">
      <div>
        <strong>Заказ №{task.order_id}</strong>
        <p className="pvz-muted">
          {task.sku} · {task.qty} шт. · {route}{task.van && ` · ${task.van}`}
          {task.variant === "hub" && <> <span className="stub-badge">ИМИТАЦИЯ</span></>}
        </p>
      </div>
      <button type="button" className="pvz-primary"
        aria-label={`${step.label} заказ №${task.order_id}`}
        disabled={refreshing || pending || alreadyHandled || uncertain}
        onClick={() => action.submit({ version: task.version })}>
        {pending ? "Выполняем…" : step.label}
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
      {tasks.error && <p role="alert" className="pvz-field-error">{tasks.error}</p>}
      {!queues && tasks.loading && <p role="status">Загружаем задания…</p>}
      {queues && !items.length && <p className="pvz-muted">Нет активных заданий.</p>}
      {items.length > 0 && (
        <ul className="pvz-task-list">
          {items.map(({ task, step }) => (
            <TaskAction
              key={`${task.order_id}-${step.action}-${task.version}`}
              task={task}
              step={step}
              refreshing={tasks.loading}
              onFeedback={(result) => {
                setFeedback(result);
                tasks.reload();
                onChanged();
              }}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
