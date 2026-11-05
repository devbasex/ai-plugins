import { useMemo } from "react";
import { createInstance, type i18n } from "i18next";
import ja from "./ja.json";
import { formatDateTime, formatMoney, formatNumber, type Money } from "./format.ts";
import { DEFAULT_LANGUAGE, useLocaleSettings } from "./settings.ts";

// 言語ごとの翻訳のファイル。言語を足すときはここへファイルを 1 つ足すだけで、画面のコードは変えない
export type TranslationResources = Record<string, { translation: Record<string, unknown> }>;

export const bundledResources: TranslationResources = { ja: { translation: ja } };

// 翻訳のファイルを同期で渡し、描く前に初期化を終える
export function createI18n(language: string, resources: TranslationResources = bundledResources): i18n {
  const instance = createInstance();
  void instance.init({
    resources: { ...bundledResources, ...resources },
    lng: language,
    fallbackLng: DEFAULT_LANGUAGE,
    initAsync: false,
    interpolation: { escapeValue: false },
  });
  return instance;
}

// 画面と部品が日時・数・金額を整形するときに使う。設定は文脈から受ける
export function useFormat() {
  const settings = useLocaleSettings();
  return useMemo(
    () => ({
      dateTime: (iso: string) => formatDateTime(iso, settings),
      number: (value: number) => formatNumber(value, settings),
      money: (money: Money) => formatMoney(money, settings),
    }),
    [settings],
  );
}
