import { describe, expect, it } from "vitest";
import { getLimits, judgeLimits, LIMITS_TABLE, type LimitsTable } from "./limits.ts";
import type { ExecutionMode, Plan, SeatType } from "./model.ts";

const ctx = (plan: Plan, seatType: SeatType | null, executionMode: ExecutionMode = "managed") => ({ plan, seatType, executionMode });

describe("getLimits", () => {
  it.each([
    ["free", null, "local", 1, null],
    ["pro", null, "managed", 3, 20_000_000],
    ["max", null, "managed", 10, 100_000_000],
    ["team", "standard", "managed", 3, 25_000_000],
    ["team", "premium", "managed", 10, 125_000_000],
  ] as const)("%s %s（%s）は表の値を返す", (plan, seat, mode, runs, tokens) => {
    expect(getLimits(ctx(plan, seat, mode))).toEqual({ concurrentSprintRuns: runs, monthlyTokens: tokens });
  });

  it.each(["local", "byok"] as const)("%s では月の token を掛けない", (mode) => {
    expect(getLimits(ctx("max", null, mode))).toEqual({ concurrentSprintRuns: 10, monthlyTokens: null });
  });

  it("表に無い組と、managed なのに月の token の値が無い組は null で、判定は断る", () => {
    expect(getLimits(ctx("team", null))).toBeNull();
    expect(getLimits(ctx("free", null, "managed"))).toBeNull();
    const j = judgeLimits(ctx("team", null), { runningSprintRuns: 0, tokensThisMonth: 0 });
    expect(j).toEqual({ allowed: false, limits: null, reasons: [{ quantity: "not_in_limits_table", limit: null, current: null }] });
  });

  it("表の値を変えると問い合わせの結果だけが変わる", () => {
    const table: LimitsTable = { ...LIMITS_TABLE, pro: { concurrentSprintRuns: 7, monthlyTokens: 5 } };
    expect(getLimits(ctx("pro", null), table)).toEqual({ concurrentSprintRuns: 7, monthlyTokens: 5 });
    expect(getLimits(ctx("max", null), table)).toEqual(getLimits(ctx("max", null)));
  });
});

describe("judgeLimits", () => {
  const pro = ctx("pro", null, "managed");

  it.each([
    [2, true],
    [3, false],
    [4, false],
  ])("同時のスプリントの実行が %i なら許すか: %s", (running, allowed) => {
    const j = judgeLimits(pro, { runningSprintRuns: running, tokensThisMonth: 0 });
    expect(j.allowed).toBe(allowed);
    expect(j.reasons).toEqual(allowed ? [] : [{ quantity: "concurrent_sprint_runs", limit: 3, current: running }]);
  });

  it.each([
    [19_999_999, true],
    [20_000_000, false],
    [20_000_001, false],
  ])("月の token が %i なら許すか: %s", (tokens, allowed) => {
    const j = judgeLimits(pro, { runningSprintRuns: 0, tokensThisMonth: tokens });
    expect(j.allowed).toBe(allowed);
    expect(j.reasons).toEqual(allowed ? [] : [{ quantity: "monthly_tokens", limit: 20_000_000, current: tokens }]);
  });

  it.each(["local", "byok"] as const)("%s では月の token が上限を超えていても断らない", (mode) => {
    const j = judgeLimits(ctx("pro", null, mode), { runningSprintRuns: 0, tokensThisMonth: 999_999_999 });
    expect(j).toEqual({ allowed: true, limits: { concurrentSprintRuns: 3, monthlyTokens: null }, reasons: [] });
  });

  it.each(["local", "byok", "managed"] as const)("%s でも同時のスプリントの数がちょうど上限なら断る", (mode) => {
    const j = judgeLimits(ctx("team", "standard", mode), { runningSprintRuns: 3, tokensThisMonth: 0 });
    expect(j.allowed).toBe(false);
    expect(j.reasons[0]).toEqual({ quantity: "concurrent_sprint_runs", limit: 3, current: 3 });
  });

  it("Team は席の種類で上限が決まる", () => {
    expect(judgeLimits(ctx("team", "premium"), { runningSprintRuns: 9, tokensThisMonth: 0 }).allowed).toBe(true);
    expect(judgeLimits(ctx("team", "standard"), { runningSprintRuns: 9, tokensThisMonth: 0 }).allowed).toBe(false);
  });
});
