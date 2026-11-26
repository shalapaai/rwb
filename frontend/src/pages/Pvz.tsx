import { useState } from "react";
import RefusalForm from "../components/pvz/RefusalForm";
import { ShelfSettings, ShelfUnits } from "../components/pvz/Shelf";
import Tasks, { type TaskQueues } from "../components/pvz/Tasks";
import { pvzPath, usePvzResource, type Network, type Stock } from "../components/pvz/pvz-api";
import "../components/pvz/pvz.css";

function PvzDesk({ pvz }: { pvz: Network["nodes"][number] }) {
  const stock = usePvzResource<Stock>(pvzPath(pvz.id, "stock"), "Не удалось загрузить полку.");
  const tasks = usePvzResource<TaskQueues>(pvzPath(pvz.id, "tasks"), "Не удалось загрузить задания.");
  const [recentUnit, setRecentUnit] = useState<number | null>(null);
  const shelf = stock.data;
  const knownSkus = [...new Set(shelf?.units.map((unit) => unit.sku))].sort();

  return (
    <div className="pvz-desk">
      <div className="pvz-cards">
        <RefusalForm pvzId={pvz.id} isOpen={pvz.is_open} knownSkus={knownSkus}
          onAccepted={(result) => {
            setRecentUnit(result.placement === "shelf" ? result.unit_id : null);
            stock.reload();
            tasks.reload();
          }} />
        {shelf && <ShelfSettings stock={shelf} refreshing={stock.loading} onSaved={stock.reload} />}
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
