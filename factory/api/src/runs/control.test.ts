import { beforeEach, describe, expect, it, vi } from "vitest";
import { validateRunJobInstruction } from "@ndf-factory/contracts";
import { ApiError } from "../errors.ts";
import { appendEvent } from "../progress/append.ts";
import { accountContext, createRunsHarness, TEST_CONFIG, type RunsHarness } from "../testing/runs.ts";
import { ResumeRejectedError, runControlConfig } from "./control.ts";

const REPO_ID = 101;
const CRED_ARN = "arn:aws:secretsmanager:us-east-1:111111111111:secret:ndf-factory/model-credentials/u-1";
let h: RunsHarness;

async function setup(harness: RunsHarness) {
  harness.github.installations.set(9, new Map([[REPO_ID, "org-a/repo-a"]]));
  harness.github.openIssues.set("org-a/repo-a", [
    { number: 1, title: "a" },
    { number: 2, title: "b" },
  ]);
  await harness.table.putRegistration({
    accountId: "acc-1",
    repositoryId: REPO_ID,
    fullName: "org-a/repo-a",
    installationId: 9,
    registeredBy: "user-1",
    registeredAt: "2026-10-30T00:00:00Z",
  });
  await harness.table.putCredential({ accountId: "acc-1", kind: "anthropic_api_key", secretArn: CRED_ARN, updatedAt: "2026-10-30T00:00:00Z" });
}

async function codeOf(p: Promise<unknown>): Promise<string> {
  try {
    await p;
  } catch (err) {
    if (err instanceof ApiError || err instanceof ResumeRejectedError) return err.code;
    throw err;
  }
  return "ok";
}

const start = (ctx = accountContext(), issues: unknown = [1]) => h.control.startSprintRun(ctx, { repositoryId: REPO_ID, issues });

// ランナーが job.finished を送った後にタスクが止まる
async function finishJob(jobId: string, sprintRunId: string, result: "gate" | "done" | "stopped" | "failed") {
  await appendEvent(h.progress, {
    sprint_run_id: sprintRunId,
    idempotency_key: `${jobId}:finished`,
    type: "job.finished",
    at: h.clock.now.toISOString(),
    source: "runner",
    job_id: jobId,
    schema_version: 1,
    payload: { result, summary: "s" },
  });
  await h.control.onTaskStateChange(jobId, "STOPPED");
}

const finishedOf = (sprintRunId: string) => h.progress.listRun(sprintRunId).filter((e) => e.type === "job.finished");

beforeEach(async () => {
  h = await createRunsHarness();
  await setup(h);
});

describe("開始（受け入れ条件 11〜14、I34・I35）", () => {
  it("実行と new のジョブができ、RunTask が 1 回。指示は契約に合い、キーの値でなく ARN だけを持つ（AC7・AC14）", async () => {
    const { sprint_run_id } = await start();
    const run = (await h.table.getRun(sprint_run_id))!;
    const job = (await h.table.getJob(run.currentJobId!))!;
    expect(job).toMatchObject({ start: "new", seq: 1, state: "pending", runnerKind: "ecs" });
    expect(h.platform.tasks.size).toBe(1);
    const task = h.platform.taskOf(job.jobId)![1];
    expect(task.mode).toBe("byok");
    expect(validateRunJobInstruction(task.instruction).ok).toBe(true);
    expect(task.instruction).toMatchObject({ START: "new", ISSUES: "1", EXECUTION_MODE: "byok", MODEL_CREDENTIAL_SECRET_ARN: CRED_ARN, NDF_VERSION: "10.26.1" });
    const token = h.platform.tokens.get(task.instruction.INGEST_TOKEN_SECRET_ARN)!;
    const verified = await h.tokens.verify(token);
    expect(verified).toMatchObject({ jobId: job.jobId, sprintRunId: sprint_run_id, repo: "org-a/repo-a" });
    expect(verified.expiresAt.getTime() - h.clock.now.getTime()).toBe(TEST_CONFIG.tokenTtlSeconds * 1000);
  });

  it("上限ちょうどで 409 limits_exceeded と reasons、1 少なければ通る。承認待ちも数える（AC11）", async () => {
    const ctx = accountContext({ plan: "pro" });
    const first = await start(ctx);
    await h.control.onTaskStateChange((await h.table.getRun(first.sprint_run_id))!.currentJobId!, "RUNNING");
    await finishJob((await h.table.getRun(first.sprint_run_id))!.currentJobId!, first.sprint_run_id, "gate");
    expect((await h.table.getRun(first.sprint_run_id))!.state).toBe("awaiting_approval");
    await start(ctx);
    await start(ctx);
    try {
      await start(ctx);
      expect.unreachable();
    } catch (err) {
      expect((err as ApiError).status).toBe(409);
      expect((err as ApiError).body().error).toMatchObject({
        code: "limits_exceeded",
        details: { reasons: [{ quantity: "concurrent_sprint_runs", limit: 3, current: 3 }] },
      });
    }
    expect(h.table.runs.size).toBe(3);
  });

  it("同時の開始でも上限を超えない（決定 5）", async () => {
    const ctx = accountContext({ plan: "pro" });
    const results = await Promise.allSettled([start(ctx), start(ctx), start(ctx), start(ctx), start(ctx)]);
    expect(results.filter((r) => r.status === "fulfilled")).toHaveLength(3);
    expect((await h.table.getSlots("acc-1", REPO_ID)).account).toBe(3);
  });

  it("managed は月の token の上限も照らす（AC11）", async () => {
    const ctx = accountContext({ plan: "pro", executionMode: "managed" });
    const first = await start(ctx);
    await appendEvent(h.progress, {
      sprint_run_id: first.sprint_run_id,
      idempotency_key: "j:1",
      type: "usage.recorded",
      at: h.clock.now.toISOString(),
      source: "runner",
      job_id: "j",
      schema_version: 1,
      payload: { model: "m", input_tokens: 15_000_000, output_tokens: 5_000_000, cost_usd: 1, step: "impl" },
    });
    const err = await start(ctx).catch((e: ApiError) => e);
    expect((err as ApiError).body().error.details).toEqual({ reasons: [{ quantity: "monthly_tokens", limit: 20_000_000, current: 20_000_000 }] });
  });

  it("byok でキーが無いと 409 model_credential_missing（AC12）", async () => {
    await h.table.deleteCredential("acc-1");
    expect(await codeOf(start())).toBe("model_credential_missing");
    expect(h.table.runs.size).toBe(0);
  });

  it("local は RunTask を呼ばず、claimLocalRunJob で 1 回だけ受け取れる（AC12）", async () => {
    const ctx = accountContext({ executionMode: "local" });
    const { sprint_run_id } = await start(ctx);
    expect(h.platform.tasks.size).toBe(0);
    const claimed = await h.control.claimLocalRunJob("acc-1");
    expect(claimed!.instruction).toMatchObject({ SPRINT_RUN_ID: sprint_run_id, EXECUTION_MODE: "local" });
    expect(claimed!.instruction.MODEL_CREDENTIAL_SECRET_ARN).toBeUndefined();
    expect((await h.tokens.verify(claimed!.token)).sprintRunId).toBe(sprint_run_id);
    expect(await h.control.claimLocalRunJob("acc-1")).toBeNull();
    expect(await h.control.claimLocalRunJob("acc-2")).toBeNull();
  });

  it("導入が届かなければ 409 installation_unreachable で実行が増えない（AC13）", async () => {
    h.github.installations.set(9, new Map());
    expect(await codeOf(start())).toBe("installation_unreachable");
    expect(h.table.runs.size).toBe(0);
  });

  it("開いていない番号は 400 invalid_issues で番号を返し、形の違う並びも 400（AC10）", async () => {
    const err = (await start(accountContext(), [1, 7]).catch((e: ApiError) => e)) as ApiError;
    expect(err.body().error).toMatchObject({ code: "invalid_issues", details: { numbers: [7] } });
    expect(await codeOf(start(accountContext(), []))).toBe("invalid_issues");
    expect(await codeOf(start(accountContext(), [1, 1]))).toBe("invalid_issues");
  });

  it("登録の無いリポジトリは 404", async () => {
    expect(await codeOf(h.control.startSprintRun(accountContext(), { repositoryId: 999, issues: [1] }))).toBe("repository_not_found");
  });

  it("起動に失敗するとジョブと実行が failed（launch_failed）になり、枠が戻る（AC14）", async () => {
    h.platform.failRunTask = true;
    const { sprint_run_id } = await start();
    const run = (await h.table.getRun(sprint_run_id))!;
    expect(run).toMatchObject({ state: "failed", failureReason: "launch_failed" });
    expect(await h.table.getSlots("acc-1", REPO_ID)).toEqual({ account: 0, repository: 0 });
    expect(finishedOf(sprint_run_id)).toHaveLength(1);
    expect(h.platform.tokens.size).toBe(0);
  });
});

describe("停止の知らせ・再開（受け入れ条件 15・19・20、I8・I9・I40）", () => {
  async function runningRun() {
    const { sprint_run_id } = await start();
    const jobId = (await h.table.getRun(sprint_run_id))!.currentJobId!;
    await h.control.onTaskStateChange(jobId, "RUNNING");
    return { sprintRunId: sprint_run_id, jobId };
  }

  it("job.finished（gate）の後の停止で承認待ちになり、再開で resume と依頼の ID を持つジョブが起動する（AC15）", async () => {
    const { sprintRunId, jobId } = await runningRun();
    expect((await h.table.getRun(sprintRunId))!.state).toBe("running");
    await finishJob(jobId, sprintRunId, "gate");
    expect((await h.table.getRun(sprintRunId))!).toMatchObject({ state: "awaiting_approval", currentJobId: null });
    expect(h.platform.tokens.size).toBe(0);
    const { jobId: next } = await h.control.requestResume({ sprintRunId, approvalRequestId: "ar-1" });
    const instruction = h.platform.taskOf(next)![1].instruction;
    expect(instruction).toMatchObject({ START: "resume", APPROVAL_REQUEST_ID: "ar-1", NDF_VERSION: "10.26.1" });
    expect(instruction.ISSUES).toBeUndefined();
    expect((await h.table.getJob(next))!.seq).toBe(2);
  });

  it("停止を待たずに再開の要求が来ると sprint_run_busy、承認待ちでない実行は not_awaiting_approval（AC15）", async () => {
    const { sprintRunId, jobId } = await runningRun();
    expect(await codeOf(h.control.requestResume({ sprintRunId, approvalRequestId: "ar-1" }))).toBe("not_awaiting_approval");
    await appendEvent(h.progress, {
      sprint_run_id: sprintRunId,
      idempotency_key: `${jobId}:finished`,
      type: "job.finished",
      at: h.clock.now.toISOString(),
      source: "runner",
      job_id: jobId,
      schema_version: 1,
      payload: { result: "gate", summary: "s" },
    });
    expect(await codeOf(h.control.requestResume({ sprintRunId, approvalRequestId: "ar-1" }))).toBe("sprint_run_busy");
    expect(await codeOf(h.control.requestResume({ sprintRunId: "nope", approvalRequestId: "ar-1" }))).toBe("not_found");
  });

  it("同時の 2 本の再開の要求は 1 本だけ通る（AC19・I9）", async () => {
    const { sprintRunId, jobId } = await runningRun();
    await finishJob(jobId, sprintRunId, "gate");
    const results = await Promise.allSettled([
      h.control.requestResume({ sprintRunId, approvalRequestId: "ar-1" }),
      h.control.requestResume({ sprintRunId, approvalRequestId: "ar-1" }),
    ]);
    expect(results.filter((r) => r.status === "fulfilled")).toHaveLength(1);
    expect((await h.table.listActiveJobs()).filter((j) => j.sprintRunId === sprintRunId)).toHaveLength(1);
  });

  it("ランナーの job.finished が無い停止では control の failed が 1 件、ある停止では書かない。2 回目の知らせは何もしない（AC20・I40）", async () => {
    const a = await runningRun();
    await h.control.onTaskStateChange(a.jobId, "STOPPED");
    await h.control.onTaskStateChange(a.jobId, "STOPPED");
    expect(finishedOf(a.sprintRunId).map((e) => [e.source, e.payload])).toEqual([["control", expect.objectContaining({ result: "failed" })]]);
    expect((await h.table.getRun(a.sprintRunId))!).toMatchObject({ state: "failed", failureReason: "task_stopped" });

    const b = await runningRun();
    await finishJob(b.jobId, b.sprintRunId, "done");
    expect(finishedOf(b.sprintRunId).map((e) => e.source)).toEqual(["runner"]);
    expect((await h.table.getRun(b.sprintRunId))!.state).toBe("done");
  });

  it("開始・終わり・2 回目の終わりの後、枠の数は終わりの状態でない実行の数に等しい（I34）", async () => {
    const a = await runningRun();
    const b = await runningRun();
    await finishJob(a.jobId, a.sprintRunId, "done");
    await h.control.onTaskStateChange(a.jobId, "STOPPED");
    const active = [...h.table.runs.values()].filter((r) => !["done", "stopped", "failed", "cancelled"].includes(r.state)).length;
    expect(await h.table.getSlots("acc-1", REPO_ID)).toEqual({ account: active, repository: active });
    expect(active).toBe(1);
    expect(b).toBeDefined();
  });

  it("ジョブを終わりにする前に止まっても、次の見回りが同じ結果でやり直し、実行は承認待ちへ移る（I40）", async () => {
    const { sprintRunId, jobId } = await runningRun();
    const original = h.table.updateJob.bind(h.table);
    h.table.updateJob = vi.fn(async () => {
      throw new Error("中断");
    });
    await expect(finishJob(jobId, sprintRunId, "gate")).rejects.toThrow("中断");
    h.table.updateJob = original;
    expect((await h.table.getJob(jobId))!.state).toBe("running");
    await h.control.onTaskStateChange(jobId, "STOPPED");
    expect((await h.table.getRun(sprintRunId))!).toMatchObject({ state: "awaiting_approval", currentJobId: null });
    expect((await h.table.getJob(jobId))!.state).toBe("gate");
  });

  it("local のジョブの終わりの知らせも同じ処理になる（#2189 の口）", async () => {
    const { sprint_run_id } = await start(accountContext({ executionMode: "local" }));
    const claimed = (await h.control.claimLocalRunJob("acc-1"))!;
    await h.control.reportLocalRunJobEnded(claimed.instruction.JOB_ID);
    expect((await h.table.getRun(sprint_run_id))!.state).toBe("failed");
    expect(finishedOf(sprint_run_id)).toHaveLength(1);
  });
});

describe("見回り（受け入れ条件 21、I8）", () => {
  it("打ち切りの時間を過ぎたジョブに StopTask し、停止の後に failed（timeout）", async () => {
    const { sprint_run_id } = await start();
    const jobId = (await h.table.getRun(sprint_run_id))!.currentJobId!;
    await h.control.onTaskStateChange(jobId, "RUNNING");
    h.clock.now = new Date(h.clock.now.getTime() + TEST_CONFIG.timeoutSeconds * 1000);
    await h.control.sweep();
    expect(h.platform.stopped).toHaveLength(0);
    h.clock.now = new Date(h.clock.now.getTime() + 1000);
    await h.control.sweep();
    expect(h.platform.stopped.map((s) => s.reason)).toEqual(["timeout"]);
    await h.control.onTaskStateChange(jobId, "STOPPED");
    expect((await h.table.getRun(sprint_run_id))!).toMatchObject({ state: "failed", failureReason: "timeout" });
  });

  it("停止の知らせを取りこぼしたジョブを拾い、job.finished が 1 件になる", async () => {
    const { sprint_run_id } = await start();
    const jobId = (await h.table.getRun(sprint_run_id))!.currentJobId!;
    await h.control.onTaskStateChange(jobId, "RUNNING");
    h.platform.taskOf(jobId)![1].status = "STOPPED";
    await h.control.sweep();
    expect((await h.table.getJob(jobId))!.state).toBe("failed");
    expect(finishedOf(sprint_run_id)).toHaveLength(1);
  });

  it("pending のまま 15 分を過ぎたジョブは failed（launch_failed）", async () => {
    const { sprint_run_id } = await start();
    h.clock.now = new Date(h.clock.now.getTime() + 16 * 60 * 1000);
    await h.control.sweep();
    expect((await h.table.getRun(sprint_run_id))!).toMatchObject({ state: "failed", failureReason: "launch_failed" });
  });
});

describe("取り消し（受け入れ条件 23）", () => {
  it("動くジョブを止め、停止の後に cancelled で枠が減る", async () => {
    const { sprint_run_id } = await start();
    const jobId = (await h.table.getRun(sprint_run_id))!.currentJobId!;
    await h.control.onTaskStateChange(jobId, "RUNNING");
    expect(await h.control.cancelSprintRun(accountContext(), sprint_run_id)).toEqual({ state: "running" });
    expect(h.platform.stopped.map((s) => s.reason)).toEqual(["cancelled"]);
    await h.control.onTaskStateChange(jobId, "STOPPED");
    expect((await h.table.getRun(sprint_run_id))!.state).toBe("cancelled");
    expect((await h.table.getSlots("acc-1", REPO_ID)).account).toBe(0);
    expect(await codeOf(h.control.cancelSprintRun(accountContext(), sprint_run_id))).toBe("already_finished");
  });

  it("承認待ちはその場で cancelled。別のアカウントからは 404", async () => {
    const { sprint_run_id } = await start();
    const jobId = (await h.table.getRun(sprint_run_id))!.currentJobId!;
    await finishJob(jobId, sprint_run_id, "gate");
    expect(await codeOf(h.control.cancelSprintRun(accountContext({ accountId: "acc-2" }), sprint_run_id))).toBe("sprint_run_not_found");
    expect(await h.control.cancelSprintRun(accountContext(), sprint_run_id)).toEqual({ state: "cancelled" });
    expect((await h.table.getSlots("acc-1", REPO_ID)).account).toBe(0);
  });
});

describe("設定（I41）", () => {
  it("token の期限が打ち切りの時間以下なら断り、既定は 28800 と 32400", () => {
    expect(() => runControlConfig({ INGEST_URL: "https://x", RUN_JOB_TIMEOUT_SECONDS: "100", RUN_JOB_TOKEN_TTL_SECONDS: "100" })).toThrow();
    expect(runControlConfig({ INGEST_URL: "https://x" })).toMatchObject({ timeoutSeconds: 28_800, tokenTtlSeconds: 32_400 });
  });
});
