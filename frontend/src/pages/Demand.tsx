/**
 * Экран «Спрос» (YR1, роль «Логист»): что продаётся в городе и какие отказы оставляем в mesh.
 * Данные — GET /catalog/products (MX1) и GET /catalog/categories (NK2). Правило допуска
 * и порог спроса считает backend; экран только показывает, фильтрует и подсвечивает.
 */

import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import CategoryChart from "../components/demand/CategoryChart";
import ProductTable from "../components/demand/ProductTable";
import "../components/demand/demand.css";
import type { CategoryStats, ProductRow } from "../components/demand/types";
import { toNumber } from "../components/model/format";

type Data = { products: ProductRow[]; categories: CategoryStats[] };

const per = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });

export default function Demand() {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [category, setCategory] = useState("");
  const [seller, setSeller] = useState("");
  const [onlyHub, setOnlyHub] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const opts = { signal: controller.signal };
    Promise.all([
      api<ProductRow[]>("/catalog/products", "logist", opts),
      api<CategoryStats[]>("/catalog/categories", "logist", opts),
    ])
      .then(([products, categories]) => setData({ products, categories }))
      .catch((e: Error) => !controller.signal.aborted && setError(e.message));
    return () => controller.abort();
  }, []);

  const rows = useMemo(
    () =>
      (data?.products ?? []).filter(
        (r) =>
          (!category || r.category_id === category) &&
          (!seller || r.seller_id === seller) &&
          (!onlyHub || !r.mesh_eligible),
      ),
    [data, category, seller, onlyHub],
  );

  if (error) return <p className="muted">Каталог недоступен: {error}. Запустите make seed.</p>;
  if (!data) return <p className="muted">Загрузка каталога…</p>;
  if (data.products.length === 0)
    return <p className="muted">Каталог пуст — засейте демо-город: make seed.</p>;

  const threshold = toNumber(data.products[0].threshold_per_day);
  const eligible = data.products.filter((r) => r.mesh_eligible).length;
  const sold = data.categories.reduce((s, c) => s + c.sales_30d, 0);
  const sellers = [...new Set(data.products.map((r) => r.seller_id))].sort();

  return (
    <div className="demand-page">
      <div>
        <h1>Спрос в городе</h1>
        <p className="demand-rule">
          Отказ остаётся на mesh-полке, только если <b>категория разрешена</b> (не крупногабарит,
          не маркировка) и <b>спрос в городе ≥ {per.format(threshold)} шт./сут</b> — порог из
          модели: ниже него mesh дороже хаба. Иначе — в хаб.
        </p>
      </div>

      <div className="demand-tiles">
        <div className="demand-tile">
          <span>Товаров в каталоге</span>
          <strong>{data.products.length}</strong>
        </div>
        <div className="demand-tile">
          <span>Отказы можно оставить в mesh</span>
          <strong>{eligible}</strong>
        </div>
        <div className="demand-tile">
          <span>Отказы едут в хаб</span>
          <strong>{data.products.length - eligible}</strong>
        </div>
        <div className="demand-tile">
          <span>Продаж в городе за 30 дней</span>
          <strong>{sold} шт.</strong>
        </div>
      </div>

      <section className="demand-card">
        <h2>Популярность категорий</h2>
        <p>Продажи во всех ПВЗ демо-города за 30 дней (синтетическая история) и доля в продажах.</p>
        <CategoryChart categories={data.categories} selected={category} onSelect={setCategory} />
      </section>

      <section className="demand-card">
        <h2>Товары по спросу</h2>
        <p>Спрос = продажи за 30 дней / 30. Красная строка — отказ этого товара едет в хаб.</p>
        <div className="demand-filters">
          <label>
            Категория
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">Все</option>
              {data.categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Продавец
            <select value={seller} onChange={(e) => setSeller(e.target.value)}>
              <option value="">Все</option>
              {sellers.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={onlyHub} onChange={(e) => setOnlyHub(e.target.checked)} />
            только «в хаб»
          </label>
          <span className="count">
            показано {rows.length} из {data.products.length}
          </span>
        </div>
        <ProductTable rows={rows} />
      </section>
    </div>
  );
}
