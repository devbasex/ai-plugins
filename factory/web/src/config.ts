// 画面の設定。vite.config.ts が環境変数から埋め込む
export interface FactoryConfig {
  cognitoDomain: string;
  cognitoClientId: string;
  webUrl: string;
  apiUrl: string;
}

declare const __FACTORY_CONFIG__: FactoryConfig;

export function embeddedConfig(): FactoryConfig {
  return __FACTORY_CONFIG__;
}
