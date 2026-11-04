// 画面向けの経路（#2186・#2347・#2172）。取り込み・キーの持ち込み・課題の候補・スプリントの実行・開発ループ。
// どの操作も操作の登録の層（operation.ts）で登録し、経路のアカウントを解決する。他人のアカウントは #2170 の I18 と同じ 403。
// 要素の形は機能から起こした契約（#2384）で、要素ごとの失敗は要素の結果になる
import type { Hono } from "hono";
import type { AuthEnv } from "../auth/resolve.ts";
import type { CredentialService } from "../credentials/service.ts";
import { InvalidRequestError } from "../errors.ts";
import type { GitHubPort } from "../github/port.ts";
import type { OrganizerControl } from "../organizer/control.ts";
import type { ProgressReader } from "../progress/read.ts";
import type { RepositoryService } from "../repositories/service.ts";
import { chooseCandidates, type CandidateModel } from "../runs/candidates.ts";
import type { RunControl } from "../runs/control.ts";
import type { LoopControl } from "../runs/loops.ts";
import type { RunsTable } from "../runs/table.ts";
import { parseIdempotencyKey } from "../idempotency.ts";
import { registerList, registerOperation } from "./operation.ts";
import { registerSprintViews, sprintRunList } from "./sprint-views.ts";

export interface FactoryDeps {
  repositories: RepositoryService;
  credentials: CredentialService;
  control: RunControl;
  loops: LoopControl;
  organizer: OrganizerControl;
  table: RunsTable;
  github: GitHubPort;
  candidateModel: CandidateModel;
  // 進捗の記録の読み出し（スプリントの一覧と詳細。#2172）
  reader: ProgressReader;
}

export function positiveInt(raw: unknown, message = "リポジトリの ID の形ではありません"): number {
  const n = typeof raw === "string" && /^\d+$/.test(raw) ? Number(raw) : raw;
  if (!Number.isInteger(n) || (n as number) < 1) throw new InvalidRequestError(message);
  return n as number;
}

function idOf(raw: unknown, message: string): string {
  if (typeof raw !== "string" || raw === "") throw new InvalidRequestError(message);
  return raw;
}

const removed = { removed: true } as const;

export function registerFactory(app: Hono<AuthEnv>, deps: () => FactoryDeps): void {
  registerList(app, "getInstallUrl", async () => [deps().repositories.installUrl()]);

  registerList(app, "getAuthorizeUrl", async (_c, ctx) => [await deps().repositories.authorizeUrl(ctx!)]);

  registerOperation(app, "postRepositoryCandidates", { run: (e, ctx) => deps().repositories.candidates(ctx!, e) });

  registerList(app, "listRepositories", async (_c, ctx) => (await deps().repositories.list(ctx!)).repositories);

  registerOperation(app, "registerRepository", { run: (e, ctx) => deps().repositories.register(ctx!, e) });

  registerOperation(app, "updateRepository", {
    run: (e, ctx) => deps().repositories.declareConcurrency(ctx!, positiveInt(e.repository_id), e),
  });

  registerOperation(app, "removeRepository", {
    run: async (e, ctx) => {
      await deps().repositories.unregister(ctx!, positiveInt(e.repository_id));
      return removed;
    },
  });

  registerList(app, "getModelCredential", async (_c, ctx) => [await deps().credentials.get(ctx!)]);

  registerOperation(app, "putModelCredential", { run: (e, ctx) => deps().credentials.put(ctx!, e) });

  registerOperation(app, "deleteModelCredential", {
    run: async (_e, ctx) => {
      await deps().credentials.delete(ctx!);
      return removed;
    },
  });

  registerOperation(app, "postIssueCandidates", {
    run: (e, ctx) => {
      const exclude = Array.isArray(e.exclude) ? e.exclude.filter((n): n is number => Number.isInteger(n)) : [];
      const d = deps();
      return chooseCandidates({ table: d.table, github: d.github, model: d.candidateModel }, ctx!, positiveInt(e.repository_id), exclude);
    },
  });

  // 同じ冪等の鍵の 2 回目は 1 回目の実行の識別子を返す（#2384 の I14）
  registerOperation(app, "startSprintRun", {
    run: (e, ctx) =>
      deps().control.startSprintRun(ctx!, { repositoryId: e.repository_id, issues: e.issues, idempotencyKey: parseIdempotencyKey(e.idempotency_key) }),
  });

  registerList(app, "listSprintRuns", async (c, ctx) => {
    const raw = c.req.query("repository_id");
    return sprintRunList(deps(), ctx!, raw === undefined ? null : positiveInt(raw));
  });

  // スプリントの詳細・工程の中身・バックログ（#2172）
  registerSprintViews(app, deps);

  registerOperation(app, "cancelSprintRun", {
    run: (e, ctx) => deps().control.cancelSprintRun(ctx!, idOf(e.sprint_run_id, "実行の ID の形ではありません")),
  });

  // 開発ループ（openapi/loops.yaml）
  registerOperation(app, "startLoop", { run: (e, ctx) => deps().loops.startLoop(ctx!, positiveInt(e.repository_id), { ...e, idempotency_key: parseIdempotencyKey(e.idempotency_key) }) });

  registerList(app, "listLoops", async (c, ctx) => (await deps().loops.listLoops(ctx!, positiveInt(c.req.query("repository_id")))).loops);

  // 無いループは今の 404（loop_not_found）のまま要素の結果になる
  registerOperation(app, "getLoop", { run: (e, ctx) => deps().loops.getLoopDetail(ctx!, idOf(e.loop_id, "ループの ID の形ではありません")) });

  registerOperation(app, "updateLoopBudget", {
    run: (e, ctx) => deps().loops.updateBudget(ctx!, idOf(e.loop_id, "ループの ID の形ではありません"), { budget: e.budget }),
  });

  registerOperation(app, "cancelLoop", { run: (e, ctx) => deps().loops.cancelLoop(ctx!, idOf(e.loop_id, "ループの ID の形ではありません")) });

  registerOperation(app, "startNextLap", { run: (e, ctx) => deps().loops.requestNextLap(ctx!, idOf(e.loop_id, "ループの ID の形ではありません")) });

  // 人の判断待ちへの答え。人だけが確定できる（扱い human）
  registerOperation(app, "answerLoopPending", {
    run: async (e, ctx) => {
      const loopId = idOf(e.loop_id, "ループの ID の形ではありません");
      const { pending } = await deps().loops.answer(ctx!, loopId, { issue: e.issue, decision: e.decision, pr: e.pr });
      return { loop_id: loopId, pending };
    },
  });

  // Factory のオーガナイザー（openapi/organizer.yaml。#2439）
  registerOperation(app, "listRepositoryApprovalRequests", {
    run: async (e, ctx) => {
      const repositoryId = positiveInt(e.repository_id);
      return { repository_id: repositoryId, ...(await deps().organizer.list(ctx!, repositoryId)) };
    },
  });

  registerList(app, "listRepositoryReleases", async (c, ctx) => {
    const raw = c.req.query("limit");
    const limit = raw === undefined ? 20 : Number(raw);
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new InvalidRequestError("limit は 1〜100 の整数です");
    return (await deps().organizer.history(ctx!, positiveInt(c.req.query("repository_id")), limit)).items;
  });

  // 止まった配布を解く。人だけが確定できる（扱い human。オーガナイザーの I66）
  registerOperation(app, "resolveStalledRelease", {
    run: (e, ctx) =>
      deps().organizer.resolve(ctx!, positiveInt(e.repository_id), positiveInt(e.seq, "まとめた配布の連番の形ではありません"), { choice: e.choice }),
  });
}
