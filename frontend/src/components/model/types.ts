/**
 * Ответы /model/* (economics, задачи N8–N9). Decimal приходит из API строкой,
 * деньги — целыми копейками. Описаны здесь, потому что api-types.ts пока не перегенерирован.
 */

export type Num = number | string; // Decimal из Pydantic — строкой

export type Parameter = {
  id: string;
  label: string;
  value: Num;
  unit: string;
  basis: string;
  source: string | null;
};

export type CostItem = {
  label: string;
  formula: string;
  inputs: Record<string, Num>;
  amount_kop: number;
};

export type Scheme = { items: CostItem[]; total_kop: number };

export type Distribution = { pvz_kop: number; seller_kop: number; mp_kop: number };

export type UnitEconomics = {
  grade: "A" | "B";
  volume_l: Num;
  demand_per_day: Num;
  shelf_days: number;
  sale_probability: Num;
  expected_days: Num;
  base: Scheme;
  mesh: Scheme;
  local_delivery_min: Num;
  hub_delivery_min: number;
  storage_payment_kop: number;
  effect_kop: number;
  effect_after_storage_fee_kop: number;
  distribution: Distribution;
};

export type StressPoint = {
  x: Num;
  mesh_profitable: boolean;
  effect_kop: number;
  effect_after_storage_fee_kop: number;
  distribution: Distribution;
};

export type Sensitivity = { param: string; unit: string; base_value: Num; points: StressPoint[] };

export type BoundaryRule = { threshold: Num | null; rule: string };

export type ParameterBoundary = {
  param: string;
  unit: string;
  search_from: Num;
  search_to: Num;
  chain: BoundaryRule;
};

export type PvzMonth = {
  share: number;
  utilization: Num;
  shelf_l: Num;
  occupied_l: Num;
  storage_tariff_kop_l_day: number;
  storage_income_kop: number;
  operations_income_kop: number;
  income_kop: number;
  space_cost_kop: number;
  labour_cost_kop: number;
  materials_cost_kop: number;
  cost_kop: number;
  profit_kop: number;
  break_even_utilization: Num | null;
};
