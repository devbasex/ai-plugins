// 本番のビルドの成果物（dist/）に、ダミーの実装とダミーデータの印が入っていないかを見る
// 終了コード: 0 無し / 1 印を見つけた / 2 dist/ が無い
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const DUMMY_MARKERS = ["ndf-factory-dummy-data", "createDummyServices"];

export function findMarkers(distDir: string): string[] {
  const hits: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else {
        const body = readFileSync(full, "utf8");
        for (const marker of DUMMY_MARKERS) if (body.includes(marker)) hits.push(`${relative(distDir, full)}: ${marker}`);
      }
    }
  };
  walk(distDir);
  return hits;
}

function main(): number {
  const distDir = resolve(dirname(fileURLToPath(import.meta.url)), "..", "dist");
  if (!existsSync(distDir)) {
    console.error("dist/ is missing. Run vite build first.");
    return 2;
  }
  const hits = findMarkers(distDir);
  for (const hit of hits) console.log(hit);
  return hits.length > 0 ? 1 : 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.exit(main());
