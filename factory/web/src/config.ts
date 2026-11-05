// 画面の設定。vite.config.ts が環境変数から埋め込む
export interface FactoryConfig {
  cognitoDomain: string;
  cognitoClientId: string;
  webUrl: string;
  apiUrl: string;
  // 一覧と詳細を読み直す間隔（ミリ秒。#2172 の決定 12）
  pollIntervalMs: number;
}

declare const __FACTORY_CONFIG__: FactoryConfig;

export function embeddedConfig(): FactoryConfig {
  return __FACTORY_CONFIG__;
}
