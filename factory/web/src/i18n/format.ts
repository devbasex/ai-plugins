import type { LocaleSettings } from "./settings.ts";

type Where = Pick<LocaleSettings, "locale" | "timeZone">;

// 金額は通貨と組でだけ扱う。数だけの金額を整形の関数へ渡せない
export interface Money {
  amount: number;
  currency: string;
}

// UTC の ISO 8601 を設定の時刻帯で整形する。読めない値はそのまま返す
export function formatDateTime(iso: string, where: Where): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(where.locale, { timeZone: where.timeZone });
}

export function formatNumber(value: number, where: Pick<LocaleSettings, "locale">): string {
  return new Intl.NumberFormat(where.locale).format(value);
}

export function formatMoney(money: Money, where: Pick<LocaleSettings, "locale">): string {
  return new Intl.NumberFormat(where.locale, { style: "currency", currency: money.currency }).format(money.amount);
}
