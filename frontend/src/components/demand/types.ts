/** Ответы каталога (MX1, NK2). Decimal приходит из API строкой — переводим в число при показе. */

export type Num = number | string;

export type ProductRow = {
  sku: string;
  name: string;
  category_id: string | null;
  category: string | null;
  seller_id: string;
  price_kop: number;
  volume_l: number;
  sales_30d: number;
  demand_per_day: Num;
  threshold_per_day: Num;
  mesh_eligible: boolean;
  reason: string;
};

export type CategoryStats = {
  id: string;
  name: string;
  sales_30d: number;
  share_pct: number;
  mesh_allowed: boolean;
  ban_reason: string | null;
};
