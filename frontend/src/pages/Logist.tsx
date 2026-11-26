import { useEffect, useState } from "react";
import { api } from "../api";
import type { paths } from "../api-types";
import NightPlan from "../components/graph/NightPlan";

type RoutingDemo = paths["/routing/demo"]["get"]["responses"][200]["content"]["application/json"];
type LoadState = { status: "loading" | "error" } | { status: "ready"; demo: RoutingDemo };

/** Логист просматривает базовую сеть и шаги ночного планирования. */
export default function Logist() {
  const [load, setLoad] = useState<LoadState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api<RoutingDemo>("/routing/demo", "logist", { signal: controller.signal })
      .then((demo) => {
        if (!controller.signal.aborted) setLoad({ status: "ready", demo });
      })
      .catch(() => {
        if (!controller.signal.aborted) setLoad({ status: "error" });
      });
    return () => controller.abort();
  }, [attempt]);

  return (
    <section className="city-map-page" aria-labelledby="city-map-heading">
      <div className="city-map-heading">
        <h1 id="city-map-heading">Схема города</h1>
        <p>От базовых маршрутов газелей WB до ночного плана</p>
      </div>
      {load.status === "loading" && <p role="status">Загружаем схему города…</p>}
      {load.status === "error" && (
        <div className="city-map-error" role="alert">
          <span>Не удалось загрузить схему города.</span>
          <button
            type="button"
            onClick={() => {
              setLoad({ status: "loading" });
              setAttempt((previous) => previous + 1);
            }}
          >
            Повторить
          </button>
        </div>
      )}
      {load.status === "ready" && <NightPlan city={load.demo.city} plan={load.demo.plan} />}
    </section>
  );
}
