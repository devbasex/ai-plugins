// ランナー向けの API（/run-jobs/*）。Cognito を通らず、実行ジョブの token で認証する。
// 導入の token の渡し直し（決定 7）と状態の置き場の署名つき URL（決定 8）だけを返す。401・403 の本文は受け口と同じ（I30）
import { Hono } from "hono";
import { ApiError } from "../errors.ts";
import type { GitHubPort } from "../github/port.ts";
import { IngestForbiddenError, IngestUnauthorizedError, IngestUnavailableError } from "../progress/errors.ts";
import { RunJobTokenError, type RunJobTokenVerifier, type VerifiedRunJob } from "../progress/run-job-token.ts";
import type { RunControl } from "./control.ts";
import type { RunnerPlatform } from "./platform.ts";
import type { RunsTable } from "./table.ts";

export interface RunnerApiDeps {
  verifier: RunJobTokenVerifier;
  control: RunControl;
  table: RunsTable;
  github: GitHubPort;
  platform: RunnerPlatform;
}

type Env = { Variables: { job: VerifiedRunJob } };

export function createRunnerApiApp(deps: () => RunnerApiDeps): Hono<Env> {
  const app = new Hono<Env>();
  app.onError((err, c) => {
    const known = err instanceof ApiError ? err : new IngestUnavailableError();
    return c.json(known.body(), known.status);
  });
  app.use("/run-jobs/*", async (c, next) => {
    const header = c.req.header("Authorization");
    if (!header?.startsWith("Bearer ")) throw new IngestUnauthorizedError();
    try {
      c.set("job", await deps().verifier(header.slice("Bearer ".length)));
    } catch (err) {
      if (err instanceof RunJobTokenError) throw new IngestUnauthorizedError();
      throw err;
    }
    await next();
  });

  // その実行のリポジトリ 1 つに絞った導入の token。ジョブが終わっていれば 403
  app.post("/run-jobs/github-token", async (c) => {
    const d = deps();
    const claims = c.get("job");
    const { run } = await d.control.activeJobFor(claims.jobId, claims.sprintRunId).catch(() => {
      throw new IngestForbiddenError();
    });
    const registration = await d.table.getRegistration(run.accountId, run.repositoryId);
    if (!registration) throw new ApiError(409, "installation_unreachable", "GitHub App の導入がこのリポジトリへ届きません");
    const token = await d.github.createInstallationToken(registration.installationId, run.repositoryId);
    if (!token) throw new ApiError(409, "installation_unreachable", "GitHub App の導入がこのリポジトリへ届きません");
    return c.json({ token: token.token, expires_at: token.expiresAt });
  });

  // 状態の置き場の 15 分の署名つき URL。鍵はその実行のものだけ
  app.post("/run-jobs/state", async (c) => {
    const d = deps();
    const claims = c.get("job");
    let body: unknown;
    try {
      body = await c.req.json();
    } catch {
      body = null;
    }
    const op = (body as { op?: unknown } | null)?.op;
    if (op !== "put" && op !== "get") throw new ApiError(400, "invalid_request", "op は put か get です");
    await d.control.activeJobFor(claims.jobId, claims.sprintRunId).catch(() => {
      throw new IngestForbiddenError();
    });
    const url = await d.platform.stateUrl(claims.sprintRunId, op);
    if (!url) throw new ApiError(404, "state_not_found", "保存した状態がありません");
    return c.json({ url: url.url, expires_at: url.expiresAt });
  });

  return app;
}
