import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";
import { loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// 画面へ埋め込む変数は次の 5 つだけ。ほかの変数を画面へ入れない（docs/specifications/ndf-factory-accounts-and-plans.md の「環境変数」）
const FACTORY_ROOT = fileURLToPath(new URL("..", import.meta.url));

function pollInterval(raw: string | undefined): number {
  const n = Number(raw);
  return Number.isInteger(n) && n > 0 ? n : 30_000;
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, FACTORY_ROOT, "");
  const config = {
    cognitoDomain: env.COGNITO_DOMAIN ?? "",
    cognitoClientId: env.COGNITO_CLIENT_ID ?? "",
    webUrl: env.FACTORY_WEB_URL ?? "",
    apiUrl: env.FACTORY_API_URL ?? "",
    // 一覧と詳細の読み直しの間隔。正の整数でなければ既定の 30 秒
    pollIntervalMs: pollInterval(env.FACTORY_POLL_INTERVAL_MS),
  };
  return {
    plugins: [react()],
    define: { __FACTORY_CONFIG__: JSON.stringify(config) },
    test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"] },
  };
});
