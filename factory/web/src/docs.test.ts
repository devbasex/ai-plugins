// @vitest-environment node
// 確定仕様の表の識別子の集合（経路・部品の名前）とコードの集合を照らす。文言は照らさない
import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { availableScreens } from "./screens.ts";

const SPECS = new URL("../../../docs/specifications/", import.meta.url);

// 見出しの節の中の表の行の、セルの配列
function tableRows(file: string, heading: string): string[][] {
  const text = readFileSync(new URL(file, SPECS), "utf8");
  const start = text.indexOf(`\n## ${heading}\n`);
  if (start < 0) throw new Error(`missing section: ${heading}`);
  const end = text.indexOf("\n## ", start + 1);
  return text
    .slice(start, end < 0 ? undefined : end)
    .split("\n")
    .filter((line) => line.startsWith("| ") && !line.startsWith("| ---"))
    .slice(1)
    .map((line) => line.slice(2, -2).split(" | "));
}

const code = (cell: string) => /^`([^`]+)`$/.exec(cell)?.[1] ?? null;

describe("確定仕様とコードの一致", () => {
  it("画面の地図の「あり」の行の経路の集合が screens.ts の available の経路の集合と一致する（受け入れ条件 1・4）", () => {
    const documented = tableRows("ndf-factory-screen-map.md", "画面の地図")
      .filter((cells) => cells[3]?.startsWith("あり"))
      .map((cells) => code(cells[0]!));
    expect(documented.toSorted()).toEqual(availableScreens().map((s) => s.path).toSorted());
  });

  it("部品の一覧の部品の集合が src/ui/ の部品のファイルの集合と一致する（受け入れ条件 6）", () => {
    const documented = tableRows("ndf-factory-ui-components.md", "部品の一覧").map((cells) => code(cells[0]!));
    const files = readdirSync(fileURLToPath(new URL("./ui/", import.meta.url)))
      .filter((f) => f.endsWith(".tsx") && !f.includes(".test."))
      .map((f) => f.replace(/\.tsx$/, ""));
    expect(documented.toSorted()).toEqual(files.toSorted());
  });
});
