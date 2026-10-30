import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";
import { loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// 画面へ埋め込む変数は次の 4 つだけ。ほかの変数を画面へ入れない（docs/specifications/ndf-factory-accounts-and-plans.md の「環境変数」）
const FACTORY_ROOT = fileURLToPath(new URL("..", import.meta.url));

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, FACTORY_ROOT, "");
  const config = {
    cognitoDomain: env.COGNITO_DOMAIN ?? "",
    cognitoClientId: env.COGNITO_CLIENT_ID ?? "",
    webUrl: env.FACTORY_WEB_URL ?? "",
    apiUrl: env.FACTORY_API_URL ?? "",
  };
  return {
    plugins: [react()],
    define: { __FACTORY_CONFIG__: JSON.stringify(config) },
    test: { environment: "jsdom" },
  };
});
