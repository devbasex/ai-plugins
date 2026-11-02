import { createContext, useContext } from "react";

// 言語と地域の設定。language は文言の言語、locale は日時と数の書式、timeZone は表示の時刻帯
export interface LocaleSettings {
  language: string;
  locale: string;
  timeZone: string;
}

export const DEFAULT_LANGUAGE = "ja";

// ブラウザの言語の並びから、翻訳のファイルがある最初の言語を選ぶ。無ければ日本語
export function pickLanguage(preferred: readonly string[], available: readonly string[]): string {
  for (const tag of preferred) {
    const lower = tag.toLowerCase();
    const hit = available.find((a) => a.toLowerCase() === lower) ?? available.find((a) => a.toLowerCase() === lower.split("-")[0]);
    if (hit) return hit;
  }
  return DEFAULT_LANGUAGE;
}

export interface BrowserLocale {
  languages?: readonly string[];
  language?: string;
}

// 利用者ごとの設定はまだ無いため、ブラウザの言語と時刻帯から決める
export function detectSettings(available: readonly string[], nav: BrowserLocale = navigator, timeZone?: string): LocaleSettings {
  const preferred = nav.languages && nav.languages.length > 0 ? nav.languages : nav.language ? [nav.language] : [];
  return {
    language: pickLanguage(preferred, available),
    locale: preferred[0] ?? "ja-JP",
    timeZone: timeZone ?? Intl.DateTimeFormat().resolvedOptions().timeZone,
  };
}

export const LocaleSettingsContext = createContext<LocaleSettings>({ language: DEFAULT_LANGUAGE, locale: "ja-JP", timeZone: "UTC" });

export function useLocaleSettings(): LocaleSettings {
  return useContext(LocaleSettingsContext);
}
