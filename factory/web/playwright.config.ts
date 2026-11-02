import { defineConfig } from "@playwright/test";

// ダミーで起動した画面を Chromium の 3 つの代表の幅で開く（jsdom は配置を計算しないため実ブラウザで測る）
const PORT = 5179;

export default defineConfig({
  testDir: "e2e",
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? "line" : "list",
  use: { baseURL: `http://localhost:${PORT}`, browserName: "chromium" },
  projects: [
    { name: "phone-360", use: { viewport: { width: 360, height: 740 }, isMobile: true, hasTouch: true } },
    { name: "tablet-768", use: { viewport: { width: 768, height: 1024 }, hasTouch: true } },
    { name: "desktop-1280", use: { viewport: { width: 1280, height: 800 } } },
  ],
  webServer: {
    command: `npx vite --mode dummy --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
  },
});
