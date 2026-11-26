/** Данные для аналитики ПВЗ и продавца. Суммы из API храним в копейках. */

import { useEffect, useState } from "react";
import { api, type Role } from "../../api";

export type Network = { nodes: { id: string; name: string; kind: string; is_open: boolean }[] };

export type PvzAnalyticsData = {
  income: { op: string; label: string; amount_kop: number }[];
  income_total_kop: number;
  costs: { resource: string; amount_kop: number }[];
  costs_total_kop: number;
  penalties_kop: number;
  profit_kop: number;
  profit_base_kop: number;
  shelf: { mesh_share_pct: number; used_l: number; limit_l: number; units: number; load_pct: number };
  ops_today: number;
  ops_limit: number;
};

export type Payout = {
  price_kop: number;
  commission_kop: number;
  logistics_kop: number;
  storage_kop: number;
  payout_kop: number;
};

export type SellerAnalyticsData = {
  orders: {
    order_id: number;
    sku: string;
    status: string;
    variant: string | null;
    mesh: Payout | null;
    base: Payout | null;
    gain_kop: number | null;
  }[];
  refusals_on_shelves: {
    unit_id: number;
    sku: string;
    pvz_id: string | null;
    grade: string | null;
    status: string;
    age_days: number;
  }[];
  summary: {
    orders: number;
    issued: number;
    resold_locally: number;
    payout_mesh_kop: number;
    payout_base_kop: number;
    gain_kop: number;
    refusals_on_shelves: number;
  };
};

export type CatalogSeller = { seller_id: string };

export type SellerStats = {
  name: string;
  sales_30d: number;
  eligible: number;
  total: number;
  products: {
    sku: string;
    name: string;
    sales_30d: number;
    demand_per_day: number | string;
    mesh_eligible: boolean;
    reason: string;
  }[];
  by_pvz: { pvz_id: string; sales_30d: number }[];
};

type Resource<T> =
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error" };

export function useAnalytics<T>(path: string, role: Role) {
  const [attempt, setAttempt] = useState(0);
  const [resource, setResource] = useState<Resource<T>>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    api<T>(path, role, { signal: controller.signal }).then(
      (data) => {
        if (!controller.signal.aborted) setResource({ status: "ready", data });
      },
      () => {
        if (!controller.signal.aborted) setResource({ status: "error" });
      },
    );
    return () => controller.abort();
  }, [path, role, attempt]);

  function retry() {
    setResource({ status: "loading" });
    setAttempt((value) => value + 1);
  }

  return { resource, retry };
}
