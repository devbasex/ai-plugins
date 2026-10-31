import { beforeEach, describe, expect, it } from "vitest";
import { handleRunControlEvent } from "../handlers/run-control.ts";
import { accountContext, createRunsHarness, type RunsHarness } from "../testing/runs.ts";
import { createRunnerApiApp } from "./runner-api.ts";

let h: RunsHarness;
let app: ReturnType<typeof createRunnerApiApp>;
let run: { sprintRunId: string; jobId: string; token: string };

beforeEach(async () => {
  h = await createRunsHarness();
  h.github.installations.set(9, new Map([[101, "org-a/repo-a"]]));
  h.github.openIssues.set("org-a/repo-a", [{ number: 1, title: "a" }]);
  await h.table.putRegistration({ accountId: "acc-1", repositoryId: 101, fullName: "org-a/repo-a", installationId: 9, registeredBy: "u", registeredAt: "t" });
  app = createRunnerApiApp(() => ({ verifier: h.tokens.verify, control: h.control, table: h.table, github: h.github, platform: h.platform }));
  const { sprint_run_id } = await h.control.startSprintRun(accountContext({ executionMode: "managed" }), { repositoryId: 101, issues: [1] });
  const jobId = (await h.table.getRun(sprint_run_id))!.currentJobId!;
  const instruction = h.platform.taskOf(jobId)![1].instruction;
  run = { sprintRunId: sprint_run_id, jobId, token: h.platform.tokens.get(instruction.INGEST_TOKEN_SECRET_ARN)! };
});

const post = (path: string, token: string | null, body: unknown = {}) =>
  app.request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  });

describe("ランナー向けの API（決定 7・8、受け入れ条件 30）", () => {
  it("その実行のリポジトリ 1 つに絞った導入の token を返し、取り直せる", async () => {
    for (let i = 0; i < 2; i++) {
      const res = await post("/run-jobs/github-token", run.token);
      expect(res.status).toBe(200);
      expect(((await res.json()) as { token: string }).token).toBe("ghs_test_9_101");
    }
    expect(h.github.tokenRequests.every((r) => r.repositoryId === 101)).toBe(true);
  });

  it("token が無い・無効なら 401、終わったジョブ・別の実行の token は 403", async () => {
    expect((await post("/run-jobs/github-token", null)).status).toBe(401);
    expect((await post("/run-jobs/github-token", "x.y.z")).status).toBe(401);
    const other = await h.tokens.sign({ jobId: run.jobId, sprintRunId: "another", repo: "org-a/repo-a" });
    expect((await post("/run-jobs/state", other, { op: "put" })).status).toBe(403);
    await h.control.onTaskStateChange(run.jobId, "STOPPED");
    const res = await post("/run-jobs/github-token", run.token);
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: { code: "forbidden", message: "この事象を書く権限がありません" } });
  });

  it("状態の置き場はその実行の鍵だけの URL。保存が無い get は 404", async () => {
    const put = (await (await post("/run-jobs/state", run.token, { op: "put" })).json()) as { url: string };
    expect(put.url).toContain(`sprint-runs/${run.sprintRunId}/`);
    expect((await post("/run-jobs/state", run.token, { op: "get" })).status).toBe(404);
    expect((await post("/run-jobs/state", run.token, { op: "drop" })).status).toBe(400);
  });
});

describe("停止の知らせと見回りの handler", () => {
  it("ECS の知らせの startedBy でジョブを引き、見回りの起動で sweep する", async () => {
    await handleRunControlEvent(h.control, { "detail-type": "ECS Task State Change", detail: { startedBy: run.jobId, lastStatus: "RUNNING" } });
    expect((await h.table.getJob(run.jobId))!.state).toBe("running");
    h.platform.taskOf(run.jobId)![1].status = "STOPPED";
    await handleRunControlEvent(h.control, { "detail-type": "Scheduled Event" });
    expect((await h.table.getJob(run.jobId))!.state).toBe("failed");
  });
});
