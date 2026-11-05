import type { AccountContext, Plan, SeatType } from "./model.ts";

// 使える量の表。値を変えるのはこの表の 1 か所だけ（前提 6、I19）
// 月の token は入力と出力の和で、cache の読み書きを含む。null は「掛けない」
export type LimitKey = Exclude<Plan, "team"> | `team:${SeatType}`;

export interface LimitRow {
  concurrentSprintRuns: number;
  monthlyTokens: number | null;
}

export type LimitsTable = Readonly<Record<LimitKey, LimitRow>>;

export const LIMITS_TABLE: LimitsTable = {
  free: { concurrentSprintRuns: 1, monthlyTokens: null },
  pro: { concurrentSprintRuns: 3, monthlyTokens: 20_000_000 },
  max: { concurrentSprintRuns: 10, monthlyTokens: 100_000_000 },
  "team:standard": { concurrentSprintRuns: 3, monthlyTokens: 25_000_000 },
  "team:premium": { concurrentSprintRuns: 10, monthlyTokens: 125_000_000 },
};

export interface Limits {
  concurrentSprintRuns: number;
  // 実行の形態が managed のときだけ数。ほかは null（掛けない、I23）
  monthlyTokens: number | null;
}

export type LimitContext = Pick<AccountContext, "plan" | "seatType" | "executionMode">;

export interface CurrentUsage {
  // 新しい 1 つを始める前に動いているスプリントの実行の数
  runningSprintRuns: number;
  // その UTC の暦月に使った token の数（チームではその席の利用者の分）。managed でなければ省いてよい
  tokensThisMonth?: number;
  // 同じリポジトリで動いているスプリントの実行の数と、登録の同時の実行の数の宣言（I49。#2347）。省けば掛けない
  runningRepoRuns?: number;
  repoMaxRuns?: number | null;
}

export type LimitQuantity = "concurrent_sprint_runs" | "concurrent_repo_runs" | "monthly_tokens" | "not_in_limits_table";

export interface LimitReason {
  quantity: LimitQuantity;
  limit: number | null;
  current: number | null;
}

export interface Judgment {
  allowed: boolean;
  limits: Limits | null;
  reasons: LimitReason[];
}

function keyOf(ctx: LimitContext): string {
  return ctx.plan === "team" ? `team:${ctx.seatType ?? ""}` : ctx.plan;
}

// 表に無い組と、managed なのに月の token の値が無い組は null（judgeLimits が断る、I19）
export function getLimits(ctx: LimitContext, table: LimitsTable = LIMITS_TABLE): Limits | null {
  const row: LimitRow | undefined = (table as Record<string, LimitRow>)[keyOf(ctx)];
  if (row === undefined) return null;
  if (ctx.executionMode !== "managed") {
    return { concurrentSprintRuns: row.concurrentSprintRuns, monthlyTokens: null };
  }
  if (row.monthlyTokens === null) return null;
  return { concurrentSprintRuns: row.concurrentSprintRuns, monthlyTokens: row.monthlyTokens };
}

// 今の値が上限より小さければ許す。上限ちょうどで断る
export function judgeLimits(
  ctx: LimitContext,
  current: CurrentUsage,
  table: LimitsTable = LIMITS_TABLE,
): Judgment {
  const limits = getLimits(ctx, table);
  if (limits === null) {
    return {
      allowed: false,
      limits: null,
      reasons: [{ quantity: "not_in_limits_table", limit: null, current: null }],
    };
  }
  const reasons: LimitReason[] = [];
  if (!(current.runningSprintRuns < limits.concurrentSprintRuns)) {
    reasons.push({
      quantity: "concurrent_sprint_runs",
      limit: limits.concurrentSprintRuns,
      current: current.runningSprintRuns,
    });
  }
  // リポジトリの同時の数は、料金プランの同時の数と登録の宣言の小さい方
  if (current.runningRepoRuns !== undefined) {
    const declared = current.repoMaxRuns ?? null;
    const repoLimit = declared === null ? limits.concurrentSprintRuns : Math.min(limits.concurrentSprintRuns, declared);
    // 宣言の無いリポジトリの上限は料金プランの値で、アカウントの上限と同時に当たる。理由を重ねない
    const accountHit = reasons.some((r) => r.quantity === "concurrent_sprint_runs");
    if (!(current.runningRepoRuns < repoLimit) && (!accountHit || repoLimit < limits.concurrentSprintRuns)) {
      reasons.push({ quantity: "concurrent_repo_runs", limit: repoLimit, current: current.runningRepoRuns });
    }
  }
  if (limits.monthlyTokens !== null) {
    const tokens = current.tokensThisMonth;
    if (tokens === undefined || !(tokens < limits.monthlyTokens)) {
      reasons.push({ quantity: "monthly_tokens", limit: limits.monthlyTokens, current: tokens ?? null });
    }
  }
  return { allowed: reasons.length === 0, limits, reasons };
}
