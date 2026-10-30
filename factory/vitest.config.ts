import { defineConfig } from "vitest/config";

// リポジトリの根から `vitest run --root factory <パス>` で web と api のテストを
// まとめて絞り込めるようにする（.ndf/project.json の suite factory の範囲テスト）
export default defineConfig({
  test: {
    projects: ["api", "web"],
  },
});
