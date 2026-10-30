import { readFile } from "node:fs/promises";
import { describe, expect, it } from "vitest";
import { generateFromSchema, generatedPath, schemaPath, staleSchemas } from "../scripts/generate.ts";

describe("生成した型（受け入れ条件 3、決定 2）", () => {
  it("コミットした型が、定義から作り直した型と一致する", async () => {
    // 落ちたら npm run generate -w contracts で作り直す
    expect(await staleSchemas()).toEqual([]);
  });

  it("定義に項目を足すと、作り直した型がコミットした型と食い違う", async () => {
    const schema = JSON.parse(await readFile(schemaPath("run-job-instruction"), "utf8"));
    schema.properties.EXTRA_INPUT = { type: "string" };
    const committed = await readFile(generatedPath("run-job-instruction"), "utf8");
    const regenerated = await generateFromSchema(schema, "run-job-instruction");
    expect(regenerated).not.toBe(committed);
    expect(regenerated).toContain("EXTRA_INPUT");
  });
});
