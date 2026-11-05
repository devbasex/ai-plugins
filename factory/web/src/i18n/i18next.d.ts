import "i18next";
import type ja from "./ja.json";

// 画面が使う翻訳のキーを日本語の翻訳のファイルのキーに絞る（無いキーは型検査で落ちる）
declare module "i18next" {
  interface CustomTypeOptions {
    defaultNS: "translation";
    resources: { translation: typeof ja };
  }
}
