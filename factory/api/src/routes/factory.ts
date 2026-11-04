// 画面向けの経路（#2186・#2347・#2172）。取り込み・キーの持ち込み・課題の候補・スプリントの実行・開発ループ。
// どの経路も resolveFromRequest でアカウントを解決し、他人のアカウントは #2170 の I18 と同じ 403
import type { Context, Hono } from "hono";
import { resolveFromRequest, type AuthEnv } from "../auth/resolve.ts";
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
import { readJson } from "./body.ts";
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

function positiveInt(raw: string | undefined): number {
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 1) throw new InvalidRequestError("リポジトリの ID の形ではありません");
  return n;
}

export function registerFactory(app: Hono<AuthEnv>, deps: () => FactoryDeps): void {
  const ctxOf = (c: Context<AuthEnv>) => resolveFromRequest(c, c.req.param("accountId"));

  app.get("/accounts/:accountId/github/install-url", async (c) => {
    await ctxOf(c);
    return c.json(deps().repositories.installUrl());
  });

  app.get("/accounts/:accountId/github/authorize-url", async (c) => {
    return c.json(await deps().repositories.authorizeUrl(await ctxOf(c)));
  });

  app.post("/accounts/:accountId/github/candidates", async (c) => {
    const ctx = await ctxOf(c);
    return c.json(await deps().repositories.candidates(ctx, await readJson(c)));
  });

  app.get("/accounts/:accountId/repositories", async (c) => {
    return c.json(await deps().repositories.list(await ctxOf(c)));
  });

  app.post("/accounts/:accountId/repositories", async (c) => {
    const ctx = await ctxOf(c);
    return c.json(await deps().repositories.register(ctx, await readJson(c)), 201);
  });

  app.patch("/accounts/:accountId/repositories/:repositoryId", async (c) => {
    const ctx = await ctxOf(c);
    return c.json(await deps().repositories.declareConcurrency(ctx, positiveInt(c.req.param("repositoryId")), await readJson(c)));
  });

  app.delete("/accounts/:accountId/repositories/:repositoryId", async (c) => {
    const ctx = await ctxOf(c);
    await deps().repositories.unregister(ctx, positiveInt(c.req.param("repositoryId")));
    return c.body(null, 204);
  });

  app.get("/accounts/:accountId/model-credential", async (c) => {
    return c.json(await deps().credentials.get(await ctxOf(c)));
  });

  app.put("/accounts/:accountId/model-credential", async (c) => {
    const ctx = await ctxOf(c);
    return c.json(await deps().credentials.put(ctx, await readJson(c)));
  });

  app.delete("/accounts/:accountId/model-credential", async (c) => {
    await deps().credentials.delete(await ctxOf(c));
    return c.body(null, 204);
  });

  app.post("/accounts/:accountId/repositories/:repositoryId/issue-candidates", async (c) => {
    const ctx = await ctxOf(c);
    const body = await readJson(c);
    const exclude = Array.isArray(body.exclude) ? body.exclude.filter((n): n is number => Number.isInteger(n)) : [];
    const d = deps();
    const list = await chooseCandidates({ table: d.table, github: d.github, model: d.candidateModel }, ctx, positiveInt(c.req.param("repositoryId")), exclude);
    return c.json(list);
  });

  app.post("/accounts/:accountId/sprint-runs", async (c) => {
    const ctx = await ctxOf(c);
    const body = await readJson(c);
    return c.json(await deps().control.startSprintRun(ctx, { repositoryId: body.repository_id, issues: body.issues }), 201);
  });

  app.get("/accounts/:accountId/sprint-runs", async (c) => {
    const ctx = await ctxOf(c);
    const raw = c.req.query("repository_id");
    return c.json({ sprint_runs: await sprintRunList(deps(), ctx, raw === undefined ? null : positiveInt(raw)) });
  });

  // スプリントの詳細・工程の中身・バックログ（#2172）
  registerSprintViews(app, deps);

  app.post("/accounts/:accountId/sprint-runs/:sprintRunId/cancel", async (c) => {
    const ctx = await ctxOf(c);
    return c.json(await deps().control.cancelSprintRun(ctx, c.req.param("sprintRunId")));
  });

  // 開発ループ（openapi/loops.yaml）
  app.post("/accounts/:accountId/repositories/:repositoryId/loops", async (c) => {
    const ctx = await ctxOf(c);
    return c.json(await deps().loops.startLoop(ctx, positiveInt(c.req.param("repositoryId")), await readJson(c)), 201);
  });

  app.get("/accounts/:accountId/repositories/:repositoryId/loops", async (c) => {
    const ctx = await ctxOf(c);
    return c.json(await deps().loops.listLoops(ctx, positiveInt(c.req.param("repositoryId"))));
  });

  app.get("/accounts/:accountId/loops/:loopId", async (c) => {
    const ctx = await ctxOf(c);
    return c.json(await deps().loops.getLoopDetail(ctx, c.req.param("loopId")));
  });

  app.patch("/accounts/:accountId/loops/:loopId", async (c) => {
    const ctx = await ctxOf(c);
    return c.json(await deps().loops.updateBudget(ctx, c.req.param("loopId"), await readJson(c)));
  });

  app.post("/accounts/:accountId/loops/:loopId/cancel", async (c) => {
    const ctx = await ctxOf(c);
    return c.json(await deps().loops.cancelLoop(ctx, c.req.param("loopId")));
  });

  app.post("/accounts/:accountId/loops/:loopId/next-lap", async (c) => {
    const ctx = await ctxOf(c);
    return c.json(await deps().loops.requestNextLap(ctx, c.req.param("loopId")), 202);
  });

  app.post("/accounts/:accountId/loops/:loopId/answers", async (c) => {
    const ctx = await ctxOf(c);
    return c.json(await deps().loops.answer(ctx, c.req.param("loopId"), await readJson(c)));
  });

  // Factory のオーガナイザー（openapi/organizer.yaml。#2439）。一覧と履歴は読むだけで、承認を確定する経路は持たない（I66）
  app.get("/accounts/:accountId/repositories/:repositoryId/approval-requests", async (c) => {
    const ctx = await ctxOf(c);
    return c.json(await deps().organizer.list(ctx, positiveInt(c.req.param("repositoryId"))));
  });

  app.get("/accounts/:accountId/repositories/:repositoryId/releases", async (c) => {
    const ctx = await ctxOf(c);
    const raw = c.req.query("limit");
    const limit = raw === undefined ? 20 : Number(raw);
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new InvalidRequestError("limit は 1〜100 の整数です");
    return c.json(await deps().organizer.history(ctx, positiveInt(c.req.param("repositoryId")), limit));
  });

  // 止まった配布を解く。画面の経路は Cognito の利用者の token だけを受ける（承認ゲートではなく失敗の扱い。決定 11）
  app.post("/accounts/:accountId/repositories/:repositoryId/releases/:seq/resolve", async (c) => {
    const ctx = await ctxOf(c);
    const seq = Number(c.req.param("seq"));
    if (!Number.isInteger(seq) || seq < 1) throw new InvalidRequestError("まとめた配布の連番の形ではありません");
    return c.json(await deps().organizer.resolve(ctx, positiveInt(c.req.param("repositoryId")), seq, await readJson(c)), 202);
  });
}
