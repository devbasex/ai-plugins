// 画面向けの経路（#2186）。取り込み・キーの持ち込み・課題の候補・スプリントの実行。
// どの経路も resolveFromRequest でアカウントを解決し、他人のアカウントは #2170 の I18 と同じ 403
import type { Context, Hono } from "hono";
import { resolveFromRequest, type AuthEnv } from "../auth/resolve.ts";
import type { CredentialService } from "../credentials/service.ts";
import { InvalidRequestError } from "../errors.ts";
import type { GitHubPort } from "../github/port.ts";
import type { RepositoryService } from "../repositories/service.ts";
import { chooseCandidates, type CandidateModel } from "../runs/candidates.ts";
import type { RunControl } from "../runs/control.ts";
import type { RunsTable } from "../runs/table.ts";
import { readJson } from "./body.ts";

export interface FactoryDeps {
  repositories: RepositoryService;
  credentials: CredentialService;
  control: RunControl;
  table: RunsTable;
  github: GitHubPort;
  candidateModel: CandidateModel;
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
    return c.json({ sprint_runs: await deps().control.listSprintRuns(ctx, raw === undefined ? null : positiveInt(raw)) });
  });

  app.post("/accounts/:accountId/sprint-runs/:sprintRunId/cancel", async (c) => {
    const ctx = await ctxOf(c);
    return c.json(await deps().control.cancelSprintRun(ctx, c.req.param("sprintRunId")));
  });
}
