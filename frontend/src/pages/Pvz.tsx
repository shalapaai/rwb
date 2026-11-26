import { useState } from "react";
import RefusalForm from "../components/pvz/RefusalForm";
import { ShelfSettings, ShelfUnits, TodayWriteOffs } from "../components/pvz/Shelf";
import Tasks, { type TaskQueues } from "../components/pvz/Tasks";
import { pvzPath, usePvzMutation, usePvzResource, type Network, type Stock } from "../components/pvz/pvz-api";
import "../components/pvz/pvz.css";

const refreshMs = 10_000;
const dayFormat = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
});

function PvzDesk({ pvz }: { pvz: Network["nodes"][number] }) {
  const stock = usePvzResource<Stock>(pvzPath(pvz.id, "stock"), "Не удалось загрузить полку.", refreshMs);
  const tasks = usePvzResource<TaskQueues>(pvzPath(pvz.id, "tasks"), "Не удалось загрузить задания.", refreshMs);
  const [recentUnit, setRecentUnit] = useState<number | null>(null);
  const [savingShare, setSavingShare] = useState(false);
  const shelf = stock.data;
  function refreshDesk() {
    stock.reload();
    tasks.reload();
  }
  const clock = usePvzMutation("/sim/clock/next", "POST", refreshDesk, refreshDesk);
  const advancing = clock.state.status === "pending";
  const retryClock = clock.state.status === "error" && clock.state.uncertain;

  return (
    <div className="pvz-desk">
      <section className="pvz-card pvz-clock" aria-label="День стенда">
        <div>
          <strong aria-live="polite">
            День стенда{shelf && (
              <> · <time dateTime={shelf.simulation_day}>{dayFormat.format(new Date(shelf.simulation_day))}</time></>
            )}
          </strong>
          <p className="pvz-muted">Переход запускает утренний расчёт для всех ПВЗ.</p>
        </div>
        <button type="button" disabled={!shelf || stock.loading || savingShare || advancing}
          onClick={() => retryClock ? clock.retry() : clock.submit({ expected_day: shelf?.simulation_day })}>
          {advancing ? "Переводим день…" : retryClock ? "Повторить запрос" : "Следующий день →"}
        </button>
        {clock.state.status === "error" && <p className="pvz-field-error" role="alert">{clock.state.message}</p>}
      </section>
      <div className="pvz-cards">
        <RefusalForm pvzId={pvz.id} isOpen={pvz.is_open} products={shelf?.products ?? []} units={shelf?.units ?? []}
          onAccepted={(result) => {
            setRecentUnit(result.placement === "hub" ? null : result.unit_id);
            refreshDesk();
          }} />
        {shelf && <ShelfSettings stock={shelf} refreshing={stock.loading || advancing}
          onSaved={refreshDesk} onSavingChange={setSavingShare} />}
        {!shelf && stock.loading && (
          <div className="pvz-card" role="status">Загружаем данные mesh-полки…</div>
        )}
      </div>
      {stock.error && (
        <div className="pvz-error" role="alert">
          <p>{stock.error}</p>
          <button type="button" onClick={stock.reload}>Обновить полку</button>
        </div>
      )}
      <Tasks tasks={tasks} onChanged={stock.reload} />
      {shelf && <ShelfUnits stock={shelf} recentUnit={recentUnit} />}
      {shelf && <TodayWriteOffs items={shelf.writeoffs_today} />}
    </div>
  );
}

/** Рабочее место оператора для приёма отказов и управления mesh-полкой. */
export default function Pvz() {
  const network = usePvzResource<Network>("/network", "Не удалось загрузить список ПВЗ.");
  const [selected, setSelected] = useState("");
  const points = network.data?.nodes.filter((node) => node.kind === "pvz") ?? [];
  const pvz = points.find((node) => node.id === selected) ?? points[0];

  return (
    <section className="pvz-page" aria-labelledby="pvz-page-title">
      <div className="pvz-heading">
        <h1 id="pvz-page-title">Оператор ПВЗ</h1>
        {pvz && (
          <label>
            Пункт выдачи
            <select value={pvz.id} onChange={(event) => setSelected(event.target.value)}>
              {points.map((node) => (
                <option key={node.id} value={node.id}>{node.name || node.id}{node.is_open ? "" : " · закрыт"}</option>
              ))}
            </select>
          </label>
        )}
      </div>
      {network.loading && <p role="status">Загружаем список ПВЗ…</p>}
      {network.error && (
        <div className="pvz-error" role="alert">
          <p>{network.error}</p>
          <button type="button" onClick={network.reload}>Повторить загрузку</button>
        </div>
      )}
      {network.data && !pvz && <p>В сети пока нет ПВЗ.</p>}
      {pvz && <PvzDesk key={pvz.id} pvz={pvz} />}
    </section>
  );
}
