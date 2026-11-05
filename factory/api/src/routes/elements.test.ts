import { describe, expect, it } from "vitest";
import { ConflictError } from "../errors.ts";
import { runElements } from "./elements.ts";

describe("要素ごとの実行（#2384 の決定 3）", () => {
  it("保存の不調などの例外もその要素の 503 にし、ほかの要素の成功の結果を残す", async () => {
    let done = 0;
    const results = await runElements([1, 2, 3], async (n) => {
      if (n === 2) throw new Error("ProvisionedThroughputExceeded");
      if (n === 3) throw new ConflictError("loop_busy", "busy");
      await new Promise((r) => setTimeout(r, 5));
      done++;
      return n;
    });
    expect(done).toBe(1);
    expect(results[0]).toEqual({ ok: true, value: 1 });
    expect(results[1]).toMatchObject({ ok: false, status: 503 });
    expect(results[2]).toMatchObject({ ok: false, status: 409, error: { code: "loop_busy" } });
  });
});
