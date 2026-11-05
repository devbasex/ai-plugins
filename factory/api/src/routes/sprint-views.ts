// スプリントの一覧・詳細・工程の中身・バックログの読み出しの経路（#2172）。読むだけで、事象の表・実行の表・GitHub へ書かない（I14）。
// アカウントは操作の登録の層が解決する。属さない実行・無い実行・無い工程・登録の無いリポジトリの要素は、
// どれも他人のアカウントと同じ本文の forbidden の結果（I12）。一覧・詳細・工程の中身は GitHub の口を呼ばず、バックログは順位の表の読み出しだけを呼ぶ（I13）
import type { Hono } from "hono";
import type { AccountContext } from "../accounts/model.ts";
import type { AuthEnv } from "../auth/resolve.ts";
import { ForbiddenError, InvalidRequestError, ServiceUnavailableError } from "../errors.ts";
import type { RankingTable } from "../github/port.ts";
import type { SprintRun } from "../runs/model.ts";
import { DETAIL_STEP_LIMIT, deriveDetail, deriveRefinements, deriveStepDetail, listRowExtras, stepRows } from "../views/derive.ts";
import { mapLimit } from "./elements.ts";
import type { FactoryDeps } from "./factory.ts";
import { positiveInt, registerOperation } from "./operation.ts";

// 一覧の行ごとの読み出しを並べる数の上限
const LIST_CONCURRENCY = 10;
// バックログで棚卸しの記録を読む実行の数（新しい順）
const BACKLOG_RUNS = 50;

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

// 一覧の行（既存の 6 項目に 4 項目を足す）。既存の経路の絞り込みと並びは実行の制御が持つ。repo は一覧で読んだ実行から取り、
// 行ごとに実行を読み直さない
export async function sprintRunList(deps: FactoryDeps, ctx: AccountContext, repositoryId: number | null) {
  const runs = await deps.control.listSprintRuns(ctx, repositoryId);
  return mapLimit(runs, LIST_CONCURRENCY, async (run) => {
    const latest = await readOrUnavailable(() => deps.reader.latestOfRun(run.sprintRunId));
    return {
      sprint_run_id: run.sprintRunId,
      repository_id: run.repositoryId,
      issues: run.issues,
      state: run.state,
      failure_reason: run.failureReason,
      created_at: run.createdAt,
      ...listRowExtras({ repo: run.repo }, latest),
    };
  });
}

export function registerSprintViews(app: Hono<AuthEnv>, deps: () => FactoryDeps): void {
  registerOperation(app, "getSprintRunDetail", {
    run: async (e, ctx) => {
      const d = deps();
      const run = await ownRun(d, ctx!, typeof e.sprint_run_id === "string" ? e.sprint_run_id : "");
      const { events, omittedSteps } = await readOrUnavailable(() => d.reader.listRunEvents(run.sprintRunId, DETAIL_STEP_LIMIT));
      return deriveDetail(run, events, omittedSteps);
    },
  });

  // 1 つの実行の中の工程の鍵を要素にする。実行は条件で、他人の実行なら要求全体が 403
  registerOperation(app, "getStepDetail", {
    prepare: async (c, ctx) => {
      const ids = c.req.queries("sprint_run_id") ?? [];
      if (ids.length !== 1) throw new InvalidRequestError("sprint_run_id を 1 つ指してください");
      const d = deps();
      const run = await ownRun(d, ctx!, ids[0]!);
      const { events } = await readOrUnavailable(() => d.reader.listRunEvents(run.sprintRunId, DETAIL_STEP_LIMIT));
      return { run, rows: stepRows(events) };
    },
    run: async (e, _ctx, { run, rows }) => {
      const key = typeof e.step_key === "string" ? e.step_key : "";
      const record = await readOrUnavailable(() => deps().reader.getStep(run.sprintRunId, key));
      if (!record) throw new ForbiddenError();
      // 上限の外の古い工程は、その工程だけの列から行を作る（フェーズは payload.phase だけ・費用は結べない）
      const row = rows.find((r) => r.key === key) ?? stepRows([record])[0]!;
      return deriveStepDetail(run, row, record);
    },
  });

  registerOperation(app, "getBacklog", {
    run: async (e, ctx) => {
      const d = deps();
      const repositoryId = positiveInt(e.repository_id);
      const registration = await d.table.getRegistration(ctx!.accountId, repositoryId);
      if (!registration) throw new ForbiddenError();
      const runs = (await d.table.listRuns(ctx!.accountId, 1000)).filter((r) => r.repositoryId === repositoryId).slice(0, BACKLOG_RUNS);
      const [ranking, perRun] = await Promise.all([
        d.github.findRankingTable(registration.installationId, repositoryId, registration.fullName).catch((): never => {
          throw new ServiceUnavailableError("github_unavailable");
        }),
        readOrUnavailable(() =>
          mapLimit(runs, LIST_CONCURRENCY, async (r) => ({ sprintRunId: r.sprintRunId, steps: await d.reader.listStepsNamed(r.sprintRunId, "refine") })),
        ),
      ]);
      return {
        repository_id: repositoryId,
        repo: registration.fullName,
        ranking: rankingOf(ranking),
        refinements: deriveRefinements(perRun),
      };
    },
  });
}

function rankingOf(table: RankingTable | null) {
  if (!table) return null;
  return { milestone: table.milestone, rows: table.rows.map((r, i) => ({ rank: i + 1, number: r.number, title: r.title })) };
}
