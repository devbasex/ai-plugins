// スプリントの一覧・詳細・工程の中身・バックログの読み出しの経路（#2172）。読むだけで、事象の表・実行の表・GitHub へ書かない（I14）。
// アカウントは resolveFromRequest で解決し、他人のアカウント・属さない実行・無い実行・無い工程・登録の無いリポジトリは
// どれも同じ本文の 403（I12）。一覧・詳細・工程の中身は GitHub の口を呼ばず、バックログは順位の表の読み出しだけを呼ぶ（I13）
import type { Context, Hono } from "hono";
import type { AccountContext } from "../accounts/model.ts";
import { resolveFromRequest, type AuthEnv } from "../auth/resolve.ts";
import { ForbiddenError, InvalidRequestError, ServiceUnavailableError } from "../errors.ts";
import type { RankingTable } from "../github/port.ts";
import type { SprintRun } from "../runs/model.ts";
import { DETAIL_STEP_LIMIT, deriveDetail, deriveRefinements, deriveStepDetail, listRowExtras, stepRows } from "../views/derive.ts";
import type { FactoryDeps } from "./factory.ts";

// 一覧の行ごとの読み出しを並べる数の上限
const LIST_CONCURRENCY = 10;
// バックログで棚卸しの記録を読む実行の数（新しい順）
const BACKLOG_RUNS = 50;

export async function mapLimit<T, R>(items: T[], limit: number, fn: (item: T) => Promise<R>): Promise<R[]> {
  const out = new Array<R>(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const i = next++;
      out[i] = await fn(items[i]!);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return out;
}

function positiveInt(raw: string | undefined): number {
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 1) throw new InvalidRequestError("リポジトリの ID の形ではありません");
  return n;
}

// 保存の失敗は 503（応答に中身を出さない）
async function readOrUnavailable<T>(read: () => Promise<T>): Promise<T> {
  try {
    return await read();
  } catch {
    throw new ServiceUnavailableError("progress_unavailable");
  }
}

// アカウントの実行だけを返す。無い実行も他人の実行も同じ 403（getRun 1 回の後に返す）
async function ownRun(deps: FactoryDeps, ctx: AccountContext, sprintRunId: string): Promise<SprintRun> {
  const run = await deps.table.getRun(sprintRunId);
  if (!run || run.accountId !== ctx.accountId) throw new ForbiddenError();
  return run;
}

// 一覧の行（既存の 6 項目に 4 項目を足す）。既存の経路の絞り込みと並びは実行の制御が持つ
export async function sprintRunList(deps: FactoryDeps, ctx: AccountContext, repositoryId: number | null) {
  const rows = await deps.control.listSprintRuns(ctx, repositoryId);
  return mapLimit(rows, LIST_CONCURRENCY, async (row) => {
    const [run, latest] = await Promise.all([deps.table.getRun(row.sprint_run_id), readOrUnavailable(() => deps.reader.latestOfRun(row.sprint_run_id))]);
    return { ...row, ...listRowExtras({ repo: run?.repo ?? "" }, latest) };
  });
}

export function registerSprintViews(app: Hono<AuthEnv>, deps: () => FactoryDeps): void {
  const ctxOf = (c: Context<AuthEnv>) => resolveFromRequest(c, c.req.param("accountId"));

  app.get("/accounts/:accountId/sprint-runs/:sprintRunId", async (c) => {
    const ctx = await ctxOf(c);
    const d = deps();
    const run = await ownRun(d, ctx, c.req.param("sprintRunId"));
    const { events, omittedSteps } = await readOrUnavailable(() => d.reader.listRunEvents(run.sprintRunId, DETAIL_STEP_LIMIT));
    return c.json(deriveDetail(run, events, omittedSteps));
  });

  app.get("/accounts/:accountId/sprint-runs/:sprintRunId/steps/:stepKey", async (c) => {
    const ctx = await ctxOf(c);
    const d = deps();
    const run = await ownRun(d, ctx, c.req.param("sprintRunId"));
    const key = c.req.param("stepKey");
    const [record, { events }] = await readOrUnavailable(() => Promise.all([d.reader.getStep(run.sprintRunId, key), d.reader.listRunEvents(run.sprintRunId, DETAIL_STEP_LIMIT)]));
    if (!record) throw new ForbiddenError();
    // 上限の外の古い工程は、その工程だけの列から行を作る（フェーズは payload.phase だけ・費用は結べない）
    const row = stepRows(events).find((r) => r.key === key) ?? stepRows([record])[0]!;
    return c.json(deriveStepDetail(run, row, record));
  });

  app.get("/accounts/:accountId/repositories/:repositoryId/backlog", async (c) => {
    const ctx = await ctxOf(c);
    const d = deps();
    const repositoryId = positiveInt(c.req.param("repositoryId"));
    const registration = await d.table.getRegistration(ctx.accountId, repositoryId);
    if (!registration) throw new ForbiddenError();
    const runs = (await d.table.listRuns(ctx.accountId, 1000)).filter((r) => r.repositoryId === repositoryId).slice(0, BACKLOG_RUNS);
    const [ranking, perRun] = await Promise.all([
      d.github.findRankingTable(registration.installationId, repositoryId, registration.fullName).catch((): never => {
        throw new ServiceUnavailableError("github_unavailable");
      }),
      readOrUnavailable(() =>
        mapLimit(runs, LIST_CONCURRENCY, async (r) => ({ sprintRunId: r.sprintRunId, steps: await d.reader.listStepsNamed(r.sprintRunId, "refine") })),
      ),
    ]);
    return c.json({
      repository_id: repositoryId,
      repo: registration.fullName,
      ranking: rankingOf(ranking),
      refinements: deriveRefinements(perRun),
    });
  });
}

function rankingOf(table: RankingTable | null) {
  if (!table) return null;
  return { milestone: table.milestone, rows: table.rows.map((r, i) => ({ rank: i + 1, number: r.number, title: r.title })) };
}
