/** Форматирование для экрана «Модель»: копейки → рубли, Decimal-строки → числа. */

import type { Num } from "./types";

const rub = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const num = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });

/** 150000 → «1 500,00 ₽»; отрицательные — со знаком минус. */
export function money(kop: number): string {
  return `${rub.format(kop / 100)} ₽`;
}

export function toNumber(value: Num): number {
  return typeof value === "number" ? value : Number(value);
}

export function plain(value: Num, digits = 3): string {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits }).format(toNumber(value));
}

export function percent(share: Num): string {
  return `${num.format(toNumber(share) * 100)}%`;
}

/** Значение допущения с единицей; копейки показываем и в рублях. */
export function withUnit(value: Num, unit: string): string {
  if (unit.startsWith("коп")) {
    const rest = unit.replace(/^коп/, "₽");
    return `${rub.format(toNumber(value) / 100)} ${rest}`;
  }
  return `${plain(value)} ${unit}`;
}
