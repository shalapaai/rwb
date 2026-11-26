/** Запуск сценариев и просмотр отчёта об изменениях в демо-городе. */

import { useRef, useState } from "react";
import { api } from "../api";
import { DataTable, LoadError } from "../components/analytics/Display";
import { useAnalytics } from "../components/analytics/data";
import "../components/analytics/analytics.css";
import { money, plain } from "../components/model/format";

type Counts = Record<string, number>;
type BeforeAfter<T> = { before: T; after: T };
type Snapshot = { shelf: Counts };
type StepValue = string | number | boolean | null | StepValue[] | { [field: string]: StepValue };
type ScenarioReport = {
  scenario: number;
  title: string;
  steps: { step: string; result: StepValue }[];
  checks: { check: string; ok: boolean }[];
  ok: boolean;
  shelf: Array<BeforeAfter<number> & { place: string }>;
  units: BeforeAfter<Counts>;
  orders: Array<{ order_id: number; before: string | null; after: string }>;
  closed_pvz: BeforeAfter<string[]>;
  ledger: { txn_count: number; unbalanced: number[]; by_account: Record<string, Counts> };
  notifications: { role: string; text: string }[];
  money_ok: boolean;
};

const statusLabels: Record<string, string> = {
  created: "Создан", on_shelf: "На полке", reserved: "Резерв", picked: "Отобран",
  in_transit: "В пути", ready: "Готов к выдаче", issued: "Выдан", to_hub: "В хаб",
  at_hub: "В хабе", cancelled: "Отменён", returned: "Возвращён", rerouted: "Перенаправлен",
  awaiting_shelf: "Ожидает полку", awaiting_return: "Ожидает вывоза", remove_pending: "К вывозу",
};

const fieldLabels: Record<string, string> = {
  order_id: "Заказ", unit_id: "Единица", status: "Статус", variant: "Маршрут",
  placement: "Размещение", decision: "Решение", source: "Откуда", pvz: "ПВЗ", pvz_id: "ПВЗ",
  reason: "Причина", cost_kop: "Себестоимость", price_kop: "Стоимость", hours: "Часы",
  penalty_kop: "Штраф", storage_kop: "Хранение", offset_days: "Сдвиг календаря, сут.",
  decided: "Решения по отказам", this_refusal: "Текущий отказ", rerouted: "Перенаправления",
  is_open: "ПВЗ открыт", used_l: "Занято, л", limit_l: "Лимит, л",
};

const routeLabels: Record<string, string> = {
  own: "Свой ПВЗ", neighbor: "Соседний ПВЗ", van: "Попутная газель",
  hub: "Хаб", shelf: "Полка", queue: "Очередь",
};

const actions: Record<string, string> = {
  "ПВЗ: pick": "ПВЗ: отбор заказа", "ПВЗ: load": "ПВЗ: погрузка в газель",
  "ПВЗ: receive": "ПВЗ: приём заказа", "ПВЗ: issue": "ПВЗ: выдача заказа",
};

const roles: Record<string, string> = {
  pvz_operator: "Оператор ПВЗ", seller: "Продавец", logist: "Логист",
  admin: "Администратор", buyer: "Покупатель",
};

const reportTable = (label: string) => [label, "До", "После"];

function stepValue(value: StepValue, field: string): string {
  if (value == null) return "—";
  if (typeof value === "number") return field.endsWith("_kop") ? money(value) : plain(value);
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  if (typeof value === "string") {
    if (field === "status") return statusLabels[value] ?? value;
    if (["variant", "placement", "decision"].includes(field)) return routeLabels[value] ?? value;
    return value;
  }
  if (Array.isArray(value)) return value.map((item) => stepValue(item, field)).join(" · ") || "—";
  return Object.entries(value)
    .filter(([key]) => key !== "imitation")
    .map(([key, item]) => `${fieldLabels[key] ?? key}: ${stepValue(item, key)}`)
    .join(" · ") || "—";
}

function Check({ ok, children }: { ok: boolean; children: string }) {
  return (
    <span className={ok ? "analytics-positive" : "analytics-negative"}>
      <span role="img" aria-label={ok ? "Успех" : "Ошибка"}>{ok ? "✓" : "✗"}</span>{" "}
      <span>{children}</span>
    </span>
  );
}

function UnitChanges({ units }: { units: BeforeAfter<Counts> }) {
  const statuses = [...new Set([...Object.keys(units.before), ...Object.keys(units.after)])].sort();
  return statuses.length ? (
    <DataTable label="Статусы единиц" columns={reportTable("Статус единицы")}>
      {statuses.map((status) => (
        <tr key={status}>
          <td>{statusLabels[status] ?? status}</td><td>{units.before[status] ?? 0}</td><td>{units.after[status] ?? 0}</td>
        </tr>
      ))}
    </DataTable>
  ) : <p className="muted">В отчёте нет единиц товара.</p>;
}

function Report({ report }: { report: ScenarioReport }) {
  const accounts = Object.entries(report.ledger.by_account).flatMap(([scheme, totals]) =>
    Object.entries(totals).map(([account, amount]) => ({ scheme, account, amount })),
  );
  return (
    <div className="analytics-report">
      <section className="analytics-card">
        <h2>{report.scenario}. {report.title}</h2>
        <p><strong><Check ok={report.ok}>{report.ok ? "Все проверки пройдены" : "Есть непройденные проверки"}</Check></strong></p>
        <h3>Шаги</h3>
        <ol>
          {report.steps.map(({ step, result }, index) => {
            const imitation = result && typeof result === "object" && !Array.isArray(result)
              && result.imitation === true;
            const title = step.replace(/:\s*ИМИТАЦИЯ.*$/u, "");
            return (
              <li key={index}>
                <strong>{actions[step] ?? title}</strong>{" "}
                {imitation && <span className="stub-badge">ИМИТАЦИЯ</span>}
                {result != null && <p className="muted">{stepValue(result, "")}</p>}
              </li>
            );
          })}
        </ol>
        <h3>Проверки</h3>
        <ul style={{ listStyle: "none", paddingLeft: 0 }}>
          {report.checks.map(({ check, ok }, index) => (
            <li key={index}><Check ok={ok}>{check}</Check></li>
          ))}
        </ul>
      </section>
      <section className="analytics-card">
        <h2>До и после</h2>
        <h3>Остатки на полках</h3>
        {report.shelf.length ? (
          <DataTable label="Изменения остатков" columns={reportTable("ПВЗ и товар")}>
            {report.shelf.map(({ place, before, after }) => (
              <tr key={place}><td>{place}</td><td>{before}</td><td>{after}</td></tr>
            ))}
          </DataTable>
        ) : <p className="muted">Остатки на полках не изменились.</p>}
        <h3>Статусы заказов</h3>
        {report.orders.length ? (
          <DataTable label="Изменения заказов" columns={reportTable("Заказ")}>
            {report.orders.map(({ order_id, before, after }) => (
              <tr key={order_id}>
                <td>№ {order_id}</td>
                <td>{before == null ? "—" : statusLabels[before] ?? before}</td>
                <td>{statusLabels[after] ?? after}</td>
              </tr>
            ))}
          </DataTable>
        ) : <p className="muted">Статусы заказов не изменились.</p>}
        <h3>Статусы единиц</h3>
        <UnitChanges units={report.units} />
        {(report.closed_pvz.before.length > 0 || report.closed_pvz.after.length > 0) && (
          <>
            <h3>Закрытые ПВЗ</h3>
            <p>До: {report.closed_pvz.before.join(", ") || "—"} · После: {report.closed_pvz.after.join(", ") || "—"}</p>
          </>
        )}
      </section>
      <section className="analytics-card">
        <h2>Проводки</h2>
        <p>Новых проводок: <strong>{report.ledger.txn_count}</strong></p>
        {report.ledger.txn_count > 0 && (
          <p><Check ok={report.money_ok}>
            {report.money_ok ? "Сумма каждой проводки равна нулю" : `Нарушен баланс проводок: ${report.ledger.unbalanced.join(", ")}`}
          </Check></p>
        )}
        {accounts.length > 0 && (
          <details>
            <summary>Изменения по счетам</summary>
            <DataTable label="Изменения по счетам" columns={["Схема", "Счёт", "Сумма"]}>
              {accounts.map(({ scheme, account, amount }) => (
                <tr key={`${scheme}:${account}`}>
                  <td>{scheme === "base" ? "Через хаб" : "Mesh"}</td><td>{account}</td>
                  <td className="analytics-money">{money(amount)}</td>
                </tr>
              ))}
            </DataTable>
          </details>
        )}
      </section>
      {report.notifications.length > 0 && (
        <section className="analytics-card">
          <h2>Уведомления</h2>
          <ul>
            {report.notifications.map(({ role, text }, index) => (
              <li key={index}><strong>{roles[role] ?? role}</strong> · {text}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

type Attempt = { scenario: string | null; key: string };
type Result = { report: ScenarioReport } | { snapshot: Snapshot };

function requestError(error: unknown) {
  const fallback = "Не удалось получить ответ от API.";
  const response = error instanceof Error ? /^(\d{3}) (.*)$/s.exec(error.message) : null;
  let message = fallback;
  if (response) {
    try {
      const { detail } = JSON.parse(response[2]);
      if (typeof detail === "string") message = detail;
    } catch {
      // Прокси может вернуть страницу ошибки вместо JSON.
    }
  }
  const code = Number(response?.[1]);
  return { message, uncertain: !code || code === 408 || code >= 500 };
}

export default function Scenarios() {
  const { resource, retry } = useAnalytics<Record<string, string>>("/scenarios", "admin");
  const [pending, setPending] = useState<Attempt | null>(null);
  const [failed, setFailed] = useState<{ attempt: Attempt; message: string; uncertain: boolean } | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const running = useRef(false);

  async function send(attempt: Attempt) {
    if (running.current) return;
    running.current = true;
    setPending(attempt);
    setFailed(null);
    setResult(null);
    const init = { method: "POST", headers: { "Idempotency-Key": attempt.key } };
    try {
      if (attempt.scenario == null) {
        setResult({ snapshot: await api<Snapshot>("/scenarios/reset", "admin", init) });
      } else {
        const path = `/scenarios/${encodeURIComponent(attempt.scenario)}/run`;
        setResult({ report: await api<ScenarioReport>(path, "admin", init) });
      }
    } catch (error) {
      setFailed({ attempt, ...requestError(error) });
    } finally {
      running.current = false;
      setPending(null);
    }
  }

  function start(scenario: string | null) {
    if (running.current || failed?.uncertain) return;
    if (scenario == null && !window.confirm("Сбросить демо-город? Заказы, остатки и проводки будут заменены исходными данными.")) return;
    void send({ scenario, key: crypto.randomUUID() });
  }

  const locked = pending != null || failed?.uncertain === true;
  const titles = resource.status === "ready" ? Object.entries(resource.data) : [];
  const snapshot = result && "snapshot" in result ? result.snapshot : null;

  return (
    <section className="analytics-page" aria-labelledby="scenarios-title">
      <div className="analytics-heading">
        <h1 id="scenarios-title">Сценарии</h1>
        <button type="button" disabled={locked} onClick={() => start(null)}>Сбросить демо-город</button>
      </div>
      {resource.status === "loading" && <p role="status">Загружаем сценарии…</p>}
      {resource.status === "error" && <LoadError message="Не удалось загрузить список сценариев." retry={retry} />}
      {resource.status === "ready" && titles.length === 0 && <p>Нет доступных сценариев.</p>}
      {titles.length > 0 && (
        <div className="analytics-columns" role="group" aria-label="Запуск сценариев">
          {titles.map(([number, title]) => (
            <button className="analytics-card" type="button" key={number} disabled={locked}
              style={{ textAlign: "left", font: "inherit", cursor: locked ? "default" : "pointer" }}
              onClick={() => start(number)}>
              <strong>{number}.</strong> {title}
            </button>
          ))}
        </div>
      )}
      {pending && <p role="status">
        {pending.scenario == null ? "Сбрасываем демо-город…" : `Выполняем сценарий № ${pending.scenario}…`}
      </p>}
      {failed && (
        <LoadError message={failed.message} retry={() => void send(failed.attempt)} />
      )}
      {result && "report" in result && <Report report={result.report} />}
      {snapshot && (
        <section className="analytics-card">
          <h2 role="status">Демо-город сброшен</h2>
          <DataTable label="Остатки после сброса" columns={["ПВЗ и товар", "На полке, шт."]}>
            {Object.entries(snapshot.shelf).map(([place, count]) => (
              <tr key={place}><td>{place}</td><td>{count}</td></tr>
            ))}
          </DataTable>
        </section>
      )}
    </section>
  );
}
