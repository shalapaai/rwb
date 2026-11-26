import { useEffect, useState } from "react";
import { api } from "../api";
import type { paths } from "../api-types";
import NightPlan from "../components/graph/NightPlan";
import WhatIf from "../components/graph/WhatIf";

type RoutingDemo = paths["/routing/demo"]["get"]["responses"][200]["content"]["application/json"];
type LoadState = { status: "loading" | "error" } | { status: "ready"; demo: RoutingDemo };

function RoutingWorkspace({ demo }: { demo: RoutingDemo }) {
  const [mode, setMode] = useState<"plan" | "what-if">("plan");
  const [source, setSource] = useState<string | null>(null);

  return (
    <>
      <div className="night-plan-steps logist-modes" role="group" aria-label="Режим схемы">
        <button type="button" aria-pressed={mode === "plan"} onClick={() => setMode("plan")}>
          Ночной план
        </button>
        <button type="button" aria-pressed={mode === "what-if"} onClick={() => setMode("what-if")}>
          Куда нужно товару
        </button>
      </div>
      <div hidden={mode !== "plan"}>
        <NightPlan city={demo.city} plan={demo.plan} onSelectPvz={(pvz) => {
          setSource(pvz);
          setMode("what-if");
        }} />
      </div>
      {mode === "what-if" && (
        <WhatIf city={demo.city} plan={demo.plan} source={source} onSelectPvz={setSource} />
      )}
    </>
  );
}

/** Логист просматривает ночной план и примеряет маршруты отдельных товаров. */
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
      {load.status === "ready" && <RoutingWorkspace demo={load.demo} />}
    </section>
  );
}
