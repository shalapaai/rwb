/**
 * Запросы экрана оператора к API сети и mesh-полки.
 * Повтор изменяющего запроса с тем же телом сохраняет исходный ключ.
 */

import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import type { components } from "../../api-types";

type Schemas = components["schemas"];
export type Stock = Schemas["StockOut"];
export type Refusal = Schemas["RefusalOut"];
export type RefusalBody = Pick<Schemas["RefusalIn"], "sku_id" | "grade" | "order_id">;
export type Settings = Schemas["SettingsOut"];
export type Network = Schemas["NetworkResponse"];

export function pvzPath(pvzId: string, endpoint: string) {
  return `/pvz/${encodeURIComponent(pvzId)}/${endpoint}`;
}

function errorInfo(error: unknown, fallback: string) {
  const response = error instanceof Error ? /^(\d{3}) (.*)$/s.exec(error.message) : null;
  const status = Number(response?.[1]);
  let message = fallback;
  if (response) {
    try {
      const { detail } = JSON.parse(response[2]);
      if (typeof detail === "string") message = detail;
    } catch {
      // Прокси может вернуть текст вместо ответа FastAPI.
    }
  }
  return { message, code: status, uncertain: !(status >= 400 && status < 500 && status !== 408) };
}

export function usePvzResource<T>(path: string, fallback: string) {
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{ data: T | null; loading: boolean; error: string | null }>(
    { data: null, loading: true, error: null },
  );

  useEffect(() => {
    const controller = new AbortController();
    api<T>(path, "pvz_operator", { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) setState({ data, loading: false, error: null });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setState((current) => ({ ...current, loading: false, error: errorInfo(error, fallback).message }));
        }
      });
    return () => controller.abort();
  }, [path, revision, fallback]);

  function reload() {
    setState((current) => ({ ...current, loading: true, error: null }));
    setRevision((current) => current + 1);
  }
  return { ...state, reload };
}

type Attempt = { key: string; body: string };
type Mutation<Output> =
  | { status: "idle" }
  | { status: "pending" }
  | { status: "success"; data: Output }
  | { status: "error"; message: string; code: number; uncertain: boolean };

export function usePvzMutation<Output>(
  path: string,
  method: "POST" | "PATCH",
  onSuccess: (data: Output) => void,
  onConflict?: (message: string) => void,
) {
  const [state, setState] = useState<Mutation<Output>>({ status: "idle" });
  const pending = useRef(false);
  const attempt = useRef<Attempt | null>(null);

  async function send(request: Attempt) {
    if (pending.current) return;
    pending.current = true;
    attempt.current = request;
    setState({ status: "pending" });
    let data: Output;
    try {
      data = await api<Output>(path, "pvz_operator", {
        method,
        headers: { "Idempotency-Key": request.key },
        body: request.body,
      });
    } catch (error) {
      const failure = errorInfo(error, "Не удалось получить ответ от API.");
      setState({ status: "error", ...failure });
      if (failure.code === 409) onConflict?.(failure.message);
      return;
    } finally {
      pending.current = false;
    }
    attempt.current = null;
    setState({ status: "success", data });
    onSuccess(data);
  }

  function submit(payload: object) {
    if (pending.current) return;
    const body = JSON.stringify(payload);
    void send(attempt.current?.body === body ? attempt.current : { body, key: crypto.randomUUID() });
  }

  function retry() {
    if (attempt.current) void send(attempt.current);
  }

  function clearFeedback() {
    if (!pending.current) setState({ status: "idle" });
  }

  return { state, submit, retry, clearFeedback };
}
