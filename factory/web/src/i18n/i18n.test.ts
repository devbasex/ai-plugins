import { describe, expect, it } from "vitest";
import type { TFunction } from "i18next";
import { createI18n } from "./index.ts";
import { formatDateTime, formatMoney, formatNumber } from "./format.ts";
import { detectSettings, pickLanguage } from "./settings.ts";

describe("日時・数・金額の整形（受け入れ条件 14・15）", () => {
  it("同じ UTC の日時を、言語と時刻帯の組ごとの時刻で整形する", () => {
    const iso = "2026-11-02T00:00:00Z";
    expect(formatDateTime(iso, { locale: "ja-JP", timeZone: "Asia/Tokyo" })).toBe("2026/11/2 9:00:00");
    const ny = formatDateTime(iso, { locale: "en-US", timeZone: "America/New_York" });
    expect(ny).toContain("11/1/2026");
    expect(ny).toMatch(/7:00:00\sPM/);
  });

  it("読めない日時はそのまま返す", () => {
    expect(formatDateTime("not-a-date", { locale: "ja-JP", timeZone: "UTC" })).toBe("not-a-date");
  });

  it("金額は通貨とともに整形し、通貨ごとに小数の桁が変わる", () => {
    expect(formatMoney({ amount: 1234.5, currency: "JPY" }, { locale: "ja-JP" })).toBe("￥1,235");
    expect(formatMoney({ amount: 1234.5, currency: "USD" }, { locale: "en-US" })).toBe("$1,234.50");
    // @ts-expect-error 通貨の無い金額は渡せない
    expect(() => formatMoney(100, { locale: "ja-JP" })).toThrow();
  });

  it("数を地域の書式で整形する", () => {
    expect(formatNumber(125_000_000, { locale: "ja-JP" })).toBe("125,000,000");
    expect(formatNumber(1234.5, { locale: "de-DE" })).toBe("1.234,5");
  });
});

describe("言語と地域の設定（前提 5・I6）", () => {
  it("翻訳のある言語を選び、無ければ日本語にする", () => {
    expect(pickLanguage(["fr-FR", "fr"], ["ja"])).toBe("ja");
    expect(pickLanguage(["en-US"], ["ja", "en"])).toBe("en");
    expect(pickLanguage([], ["ja", "en"])).toBe("ja");
  });

  it("ブラウザの言語と時刻帯から設定を決める", () => {
    expect(detectSettings(["ja"], { languages: ["fr-FR"] }, "Europe/Paris")).toEqual({ language: "ja", locale: "fr-FR", timeZone: "Europe/Paris" });
  });

  it("翻訳の無い言語では日本語の文言を出す", () => {
    expect(createI18n("fr").t("common.loading")).toBe("読み込み中");
  });
});

describe("翻訳のキーの型（受け入れ条件 12）", () => {
  it("翻訳のファイルに無いキーは型検査で落ちる", () => {
    const t: TFunction = createI18n("ja").t;
    expect(t("nav.menu")).toBe("メニュー");
    // @ts-expect-error 日本語の翻訳のファイルに無いキー
    t("nav.noSuchKey");
  });
});
