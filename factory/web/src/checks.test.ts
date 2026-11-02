// @vitest-environment node
import { describe, expect, it } from "vitest";
import { fileURLToPath } from "node:url";
import { checkSource, checkTree } from "../scripts/check-source.ts";

const rules = (file: string, code: string) => checkSource(file, code).map((f) => f.rule);

describe("直に書いた文言の検査（受け入れ条件 10・11）", () => {
  it.each([
    ["jsx-text", `export const A = () => <p>Hello</p>;`],
    ["jsx-attr", `export const A = () => <input placeholder="Search" />;`],
    ["jsx-attr", `export const A = () => <img alt={"logo"} />;`],
    ["jsx-expr", `export const A = () => <p>{"Hello"}</p>;`],
    ["jsx-expr", `export const A = (n: number) => <p>{\`items \${n}\`}</p>;`],
    ["non-ascii", `export const message = "読み込み中";`],
  ])("%s を落とす", (rule, code) => {
    expect(rules("pages/A.tsx", code)).toEqual([rule]);
  });

  it("翻訳のキーで引いた源・記号だけの文・class の値・コメントは落とさない", () => {
    const code = [
      `// コメントの中の日本語は見ない`,
      `export const A = ({ t, n }: { t: (k: string) => string; n: number }) => (`,
      `  <p className="ui-x" aria-label={t("a.b")}>`,
      `    {t("a.c")} #{n} {" "} {\`#\${n}\`}`,
      `  </p>`,
      `);`,
    ].join("\n");
    expect(rules("pages/A.tsx", code)).toEqual([]);
  });

  it("位置を 1 から数えて返す", () => {
    expect(checkSource("pages/A.tsx", `const a = 1;\nexport const B = () => <b>x</b>;`)[0]).toMatchObject({ line: 2, column: 27, text: "x" });
  });

  it("構文解析できない源は投げる", () => {
    expect(() => checkSource("pages/A.tsx", "export const = ;")).toThrow();
  });
});

describe("ダミーの import の検査（受け入れ条件 17）", () => {
  it("画面のファイルからダミーを import すると落とし、起動の組み立てだけは許す", () => {
    expect(rules("pages/A.tsx", `import { createDummyApi } from "../dummy/index.ts";`)).toEqual(["dummy-import"]);
    expect(rules("pages/A.tsx", `const m = import("../dummy/api.ts");`)).toEqual(["dummy-import"]);
    expect(rules("main.tsx", `const m = import("./dummy/index.ts");`)).toEqual([]);
  });
});

describe("今の源", () => {
  it("検査で何も見つからない", () => {
    expect(checkTree(fileURLToPath(new URL(".", import.meta.url)))).toEqual([]);
  });
});
