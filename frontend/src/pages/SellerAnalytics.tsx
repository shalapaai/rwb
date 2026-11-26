import { Fragment, useState } from "react";
import { DataTable, LoadError, Metrics } from "../components/analytics/Display";
import {
  useAnalytics, type CatalogSeller, type Payout, type SellerAnalyticsData, type SellerStats,
} from "../components/analytics/data";
import "../components/analytics/analytics.css";
import { money, plain } from "../components/model/format";

const orderStatus: Record<string, string> = {
  created: "Создан", reserved: "Резерв", picked: "Отобран", in_transit: "В пути",
  ready: "Готов к выдаче", issued: "Выдан", cancelled: "Отменён",
  returned: "Возвращён", rerouted: "Перенаправлен",
};

const shelfStatus: Record<string, string> = {
  on_shelf: "На полке", reserved: "В резерве", remove_pending: "К вывозу",
};

function PaymentCells({ data }: { data: Payout | null }) {
  const amounts = [data?.price_kop, data?.commission_kop, data?.logistics_kop, data?.storage_kop, data?.payout_kop];
  return <>{amounts.map((amount, index) => (
    <td key={index} className="analytics-money">{amount == null ? "—" : money(amount)}</td>
  ))}</>;
}

function SellerPayments({ id }: { id: string }) {
  const { resource, retry } = useAnalytics<SellerAnalyticsData>(
    `/analytics/seller/${encodeURIComponent(id)}`, "seller",
  );
  if (resource.status === "loading") return <p role="status">Загружаем выплаты и отказы…</p>;
  if (resource.status === "error") return <LoadError message="Не удалось загрузить выплаты и отказы." retry={retry} />;
  const { orders, refusals_on_shelves, summary } = resource.data;

  return (
    <>
      <Metrics values={[
        ["Выдано заказов", `${summary.issued} из ${summary.orders}`],
        ["Перепродано в городе", summary.resold_locally],
        ["Выплаты mesh", money(summary.payout_mesh_kop)],
        ["Выплаты через хаб", money(summary.payout_base_kop)],
        ["Разница выплат", money(summary.gain_kop)],
        ["Отказов на полках", summary.refusals_on_shelves],
      ]} />
      <section className="analytics-card">
        <h2>Выплаты по заказам</h2>
        {orders.length ? (
          <DataTable label="Выплаты по заказам" columns={[
            "Заказ", "Товар", "Статус", "Схема", "Цена", "Комиссия", "Логистика", "Хранение", "Выплата", "Разница",
          ]}>
            {orders.map((order) => (
              <Fragment key={order.order_id}>
                <tr>
                  <td rowSpan={2}>№ {order.order_id}</td>
                  <td rowSpan={2}>{order.sku}</td>
                  <td rowSpan={2}>{orderStatus[order.status] ?? order.status}</td>
                  <td>{order.variant === "hub" ? "Через хаб (факт)" : "Mesh"}</td>
                  <PaymentCells data={order.mesh} />
                  <td rowSpan={2} className="analytics-money">
                    {order.gain_kop == null ? "—" : money(order.gain_kop)}
                  </td>
                </tr>
                <tr><td>Через хаб (база)</td><PaymentCells data={order.base} /></tr>
              </Fragment>
            ))}
          </DataTable>
        ) : <p className="muted">По заказам пока нет проводок.</p>}
      </section>
      <section className="analytics-card">
        <h2>Отказы на mesh-полках</h2>
        {refusals_on_shelves.length ? (
          <DataTable label="Отказы на mesh-полках" columns={["Единица", "Товар", "ПВЗ", "Оценка", "Дней на полке", "Статус"]}>
            {refusals_on_shelves.map((unit) => (
              <tr key={unit.unit_id}>
                <td>№ {unit.unit_id}</td><td>{unit.sku}</td><td>{unit.pvz_id ?? "—"}</td>
                <td>{unit.grade ?? "—"}</td><td>{unit.age_days}</td>
                <td>{shelfStatus[unit.status] ?? unit.status}</td>
              </tr>
            ))}
          </DataTable>
        ) : <p className="muted">У продавца пока нет отказов на полках.</p>}
      </section>
    </>
  );
}

function CitySales({ id }: { id: string }) {
  const { resource, retry } = useAnalytics<SellerStats>(`/sellers/${encodeURIComponent(id)}/stats`, "seller");

  return (
    <section className="analytics-card">
      <h2>Мои товары в городе</h2>
      {resource.status === "loading" && <p role="status">Загружаем продажи за 30 дней…</p>}
      {resource.status === "error" && <LoadError message="Не удалось загрузить продажи в городе." retry={retry} />}
      {resource.status === "ready" && (
        <>
          <p><strong>{resource.data.name || id}</strong> · Продано за 30 дней: {resource.data.sales_30d} шт.</p>
          <p>Допущено в mesh: {resource.data.eligible} из {resource.data.total} товаров.</p>
          {resource.data.products.length > 0 && (
            <DataTable label="Продажи по товарам" columns={["Артикул", "Товар", "Продано, шт.", "Спрос, шт./сут.", "Допуск", "Причина"]}>
              {resource.data.products.map((product) => (
                <tr key={product.sku}>
                  <td>{product.sku}</td><td>{product.name || "—"}</td><td>{product.sales_30d}</td>
                  <td>{plain(product.demand_per_day)}</td>
                  <td className={product.mesh_eligible ? "analytics-positive" : "analytics-negative"}>
                    {product.mesh_eligible ? "Допущен" : "Запрещён"}
                  </td>
                  <td>{product.reason}</td>
                </tr>
              ))}
            </DataTable>
          )}
          {resource.data.by_pvz.length > 0 && (
            <>
              <h3>Продажи за 30 дней по ПВЗ</h3>
              <DataTable label="Продажи по ПВЗ" columns={["ПВЗ", "Продано, шт."]}>
                {resource.data.by_pvz.map((point) => (
                  <tr key={point.pvz_id}><td>{point.pvz_id}</td><td>{point.sales_30d}</td></tr>
                ))}
              </DataTable>
            </>
          )}
        </>
      )}
    </section>
  );
}

/** Выплаты и отказы продавца, спрос и допуск его товаров в mesh. */
export default function SellerAnalytics() {
  const { resource, retry } = useAnalytics<CatalogSeller[]>("/catalog/products", "seller");
  const [selected, setSelected] = useState("");
  const sellers = resource.status === "ready" ? [...new Set(resource.data.map((item) => item.seller_id))].sort() : [];
  const seller = sellers.includes(selected) ? selected : sellers[0];

  return (
    <section className="analytics-page" aria-labelledby="seller-analytics-title">
      <div className="analytics-heading">
        <h1 id="seller-analytics-title">Аналитика продавца</h1>
        {seller && (
          <label>
            Продавец
            <select value={seller} onChange={(event) => setSelected(event.target.value)}>
              {sellers.map((id) => <option key={id} value={id}>{id}</option>)}
            </select>
          </label>
        )}
      </div>
      {resource.status === "loading" && <p role="status">Загружаем список продавцов…</p>}
      {resource.status === "error" && <LoadError message="Не удалось загрузить список продавцов." retry={retry} />}
      {resource.status === "ready" && !seller && <p>В каталоге пока нет продавцов с товарами.</p>}
      {seller && (
        <div className="analytics-report" key={seller}>
          <SellerPayments id={seller} />
          <CitySales id={seller} />
        </div>
      )}
    </section>
  );
}
