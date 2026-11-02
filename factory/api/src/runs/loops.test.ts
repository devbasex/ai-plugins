// #2347: 開発ループ・並行する実行の取り合い・利用上限の後の再開・手元のランナーの枠（設計の「テスト設計」）
import { readdirSync, readFileSync } from "node:fs";
import { beforeEach, describe, expect, it } from "vitest";
import { validateRunJobInstruction, type RunJobInstruction } from "@ndf-factory/contracts";
import type { AccountContext } from "../accounts/model.ts";
import { ApiError } from "../errors.ts";
import { appendEvent } from "../progress/append.ts";
import { memoryProgressReader } from "../progress/read.ts";
import { accountContext, createRunsHarness, TEST_CONFIG, type RunsHarness } from "../testing/runs.ts";
import { backfill } from "./backfill.ts";
import type { RunControlConfig } from "./control.ts";
import { LoopControl, loopStateAfter, type RunOutcome } from "./loops.ts";
import { compareVersions, isLegacyVersion } from "./version.ts";

const REPO = 101;
const OTHER_REPO = 102;
const CRED_ARN = "arn:aws:secretsmanager:us-east-1:111111111111:secret:ndf-factory/model-credentials/u-1";
// 課題 → マイルストーン（null は無し）
const MILESTONES: Record<number, string | null> = { 1: "25 Factory", 2: "25 Factory", 3: "26 根本", 4: null, 5: null };

let h: RunsHarness;
let loops: LoopControl;
let jobSeq = 0;

async function setup(config: Partial<RunControlConfig> = {}, migrated = true) {
  h = await createRunsHarness({ ...TEST_CONFIG, parallelMinVersion: "10.0.0", ...config });
  h.table.migrated = migrated;
  loops = new LoopControl({ table: h.table, control: h.control, reader: memoryProgressReader(h.progress), now: () => h.clock.now, log: () => undefined });
  h.control.setLoopHooks(loops.hooks());
  h.github.installations.set(9, new Map([[REPO, "org-a/repo-a"], [OTHER_REPO, "org-a/repo-b"]]));
  for (const name of ["org-a/repo-a", "org-a/repo-b"]) {
    h.github.openIssues.set(name, [1, 2, 3, 4, 5].map((n) => ({ number: n, title: `t${n}` })));
  }
  const check = h.github.checkOpenIssues.bind(h.github);
  h.github.checkOpenIssues = async (i, r, f, numbers) => ({
    ...(await check(i, r, f, numbers)),
    milestones: Object.fromEntries(numbers.map((n) => [n, MILESTONES[n] ?? null])),
  });
  for (const repositoryId of [REPO, OTHER_REPO]) {
    await h.table.putRegistration({
      accountId: "acc-1",
      repositoryId,
      fullName: repositoryId === REPO ? "org-a/repo-a" : "org-a/repo-b",
      installationId: 9,
      registeredBy: "user-1",
      registeredAt: "2026-11-02T00:00:00Z",
    });
  }
  await h.table.putCredential({ accountId: "acc-1", kind: "anthropic_api_key", secretArn: CRED_ARN, updatedAt: "2026-11-02T00:00:00Z" });
}

async function codeOf(p: Promise<unknown>): Promise<string> {
  try {
    await p;
  } catch (err) {
    if (err instanceof ApiError) return err.code;
    throw err;
  }
  return "ok";
}

async function detailsOf(p: Promise<unknown>): Promise<Record<string, unknown>> {
  try {
    await p;
  } catch (err) {
    if (err instanceof ApiError) return (err.body().error as { details?: Record<string, unknown> }).details ?? {};
    throw err;
  }
  return {};
}

const startLoop = (name: string, milestones: string[], ctx: AccountContext = accountContext(), budget?: Record<string, number>) =>
  loops.startLoop(ctx, REPO, { name, milestones, ...(budget ? { budget } : {}) });
const manual = (issues: number[], repositoryId = REPO, ctx: AccountContext = accountContext()) => h.control.startSprintRun(ctx, { repositoryId, issues });
const loopOf = async (id: string) => (await h.table.getLoop("acc-1", id))!;
const runOf = async (id: string) => (await h.table.getRun(id))!;
const instructionOf = (jobId: string): RunJobInstruction => h.platform.taskOf(jobId)![1].instruction;

async function event(sprintRunId: string, jobId: string, type: string, payload: Record<string, unknown>) {
  await appendEvent(h.progress, {
    sprint_run_id: sprintRunId,
    idempotency_key: `${jobId}:${type === "job.finished" ? "finished" : ++jobSeq}`,
    type,
    at: h.clock.now.toISOString(),
    source: "runner",
    job_id: jobId,
    schema_version: 1,
    payload,
  } as never);
}

const lap = (n: number, extra: Record<string, unknown> = {}) => ({
  loop: "lfactory2",
  lap: n,
  issues: [1],
  result: "完了",
  version: "1.0.0-dev.1",
  cost_usd: 2,
  seconds: 1800,
  pending: [],
  ...extra,
});

// ECS のジョブを終える（ランナーの事象 → タスクの停止）
async function finishEcs(sprintRunId: string, payload: Record<string, unknown>, before: [string, Record<string, unknown>][] = []) {
  const jobId = (await runOf(sprintRunId)).currentJobId!;
  await h.control.onTaskStateChange(jobId, "RUNNING");
  for (const [type, p] of before) await event(sprintRunId, jobId, type, p);
  await event(sprintRunId, jobId, "job.finished", payload);
  await h.control.onTaskStateChange(jobId, "STOPPED");
  return jobId;
}

describe("決定 4 の表（受け入れ条件 2、I53）", () => {
  const o = (runState: RunOutcome["runState"], condition: string | null = null): RunOutcome => ({
    runState,
    failureReason: runState === "failed" ? "job_failed" : null,
    finishReason: runState === "stopped" ? "section_limit" : null,
    laps: [],
    stopped: condition ? { loop: "l", condition: condition as never, detail: "{}", pending: [] } : null,
  });
  it.each([
    [o("awaiting_approval"), "awaiting_approval", null],
    [o("awaiting_approval", "gate2_returned"), "awaiting_approval", null],
    [o("awaiting_reset"), "running", null],
    [o("done"), "between_laps", null],
    [o("done", "budget_usd"), "stopped", "budget_usd"],
    [o("stopped", "no_issue"), "stopped", "no_issue"],
    [o("failed"), "stopped", "run_failed"],
    [o("stopped"), "stopped", "run_stopped"],
    [o("cancelled"), "cancelled", null],
    [o("done", "gate2_returned"), "stopped", "run_failed"],
    [o("failed", "same_failure"), "stopped", "run_failed"],
  ])("%# の組は表のとおりに写る", (outcome, state, condition) => {
    expect(loopStateAfter(outcome, false)).toMatchObject({ state, condition });
  });
});

describe("版の比べ方（決定 12）", () => {
  it("@ より前を semver として読み、開発版の接尾辞は小さく読む。下限が無ければ古い版", () => {
    expect(compareVersions("10.27.0-dev.3@abc", "10.27.0")).toBeLessThan(0);
    expect(compareVersions("10.27.1", "10.27.0")).toBeGreaterThan(0);
    expect(compareVersions("10.27.0-dev.10", "10.27.0-dev.9")).toBeGreaterThan(0);
    expect(isLegacyVersion("10.27.0", null)).toBe(true);
    expect(isLegacyVersion("10.27.0", "10.27.0-dev.1")).toBe(false);
  });
});

describe("開発ループを始め・止め・予算を変える（受け入れ条件 1・11）", () => {
  beforeEach(() => setup());

  it("始めると周回の実行が START=loop・LOOP_NAME・マイルストーン・予算つきで起き、指示は契約に合う", async () => {
    const { loop_id, state } = await startLoop("lfactory2", ["25 Factory", "26 根本"], accountContext(), { laps: 3, usd: 60 });
    expect(state).toBe("running");
    const loop = await loopOf(loop_id);
    const run = await runOf(loop.currentRunId!);
    expect(run).toMatchObject({ loopId: loop_id, lap: 1, issues: [] });
    const ins = instructionOf(run.currentJobId!);
    expect(ins).toMatchObject({ START: "loop", LOOP_NAME: "lfactory2", LOOP_MILESTONES: "25 Factory,26 根本", LOOP_BUDGET: "laps=3,usd=60" });
    expect(ins.ISSUES).toBeUndefined();
    expect(validateRunJobInstruction(ins).ok).toBe(true);
  });

  it("予算を変えると次の周回の指示へ写り、一覧は締めた周回の和で残りを出す", async () => {
    const { loop_id } = await startLoop("lfactory2", ["25 Factory"], accountContext(), { usd: 10 });
    await loops.updateBudget(accountContext(), loop_id, { budget: { usd: 50, laps: 4 } });
    const first = (await loopOf(loop_id)).currentRunId!;
    await finishEcs(first, { result: "done", summary: "" }, [["loop.lap_closed", lap(1)]]);
    const loop = await loopOf(loop_id);
    expect(loop).toMatchObject({ state: "running", lapsClosed: 1, closedCostUsd: 2, lapRuns: 2 });
    expect(instructionOf((await runOf(loop.currentRunId!)).currentJobId!).LOOP_BUDGET).toBe("laps=4,usd=50");
    const row = (await loops.listLoops(accountContext(), REPO)).loops[0]!;
    expect(row).toMatchObject({ name: "lfactory2", laps_closed: 1, cost_usd: 2, budget_remaining: { laps: 3, usd: 48, hours: null } });
  });

  it("形の違う名前・マイルストーン・予算は 400、同じ名前の終わっていないループは 409", async () => {
    expect(await codeOf(startLoop("-bad", ["25 Factory"]))).toBe("invalid_request");
    expect(await codeOf(startLoop("l1", []))).toBe("invalid_request");
    expect(await codeOf(startLoop("l1", ["a,b"]))).toBe("invalid_request");
    expect(await codeOf(startLoop("l1", ["25 Factory"], accountContext(), { usd: 0 }))).toBe("invalid_request");
    await startLoop("l1", ["25 Factory"]);
    expect(await codeOf(startLoop("l1", ["26 根本"]))).toBe("loop_name_in_use");
  });

  it("止めると今の実行を取り消し、停止の後に cancelled へ移り、マイルストーンの持ち分が消える", async () => {
    const { loop_id } = await startLoop("l1", ["25 Factory"]);
    const runId = (await loopOf(loop_id)).currentRunId!;
    expect((await loops.cancelLoop(accountContext(), loop_id)).state).toBe("running");
    await h.control.onTaskStateChange((await runOf(runId)).currentJobId!, "STOPPED");
    expect((await loopOf(loop_id)).state).toBe("cancelled");
    expect(await h.table.getMilestoneShare("acc-1", REPO, "25 Factory")).toBeNull();
    expect(await codeOf(loops.cancelLoop(accountContext(), loop_id))).toBe("already_finished");
  });

  it("取り消しの要求を書けなければ読み直して止め直し、成功を返して取り逃さない", async () => {
    const { loop_id } = await startLoop("l1", ["25 Factory"]);
    const runId = (await loopOf(loop_id)).currentRunId!;
    const write = h.table.writeLoop.bind(h.table);
    let refused = 0;
    h.table.writeLoop = async (w) => (w.set.cancelRequested && refused++ === 0 ? false : write(w));
    await loops.cancelLoop(accountContext(), loop_id);
    expect(refused).toBe(2);
    expect((await loopOf(loop_id)).cancelRequested).toBe(true);
    expect((await runOf(runId)).cancelRequested).toBe(true);
  });
});

describe("止まる条件の写し（受け入れ条件 2）", () => {
  beforeEach(() => setup());

  it.each(["user_stop", "mvv_changed", "same_failure", "budget_laps", "budget_usd", "budget_hours", "no_issue", "outside_mvv", "refused"])(
    "%s は stopped と理由になり、持ち分が消える",
    async (condition) => {
      const { loop_id } = await startLoop("l1", ["25 Factory"]);
      const runId = (await loopOf(loop_id)).currentRunId!;
      await finishEcs(runId, { result: "done", summary: "" }, [["loop.stopped", { loop: "l1", condition, detail: '{"values":{}}', pending: [] }]]);
      expect(await loopOf(loop_id)).toMatchObject({ state: "stopped", stopCondition: condition, currentRunId: null });
      expect(await h.table.getMilestoneShare("acc-1", REPO, "25 Factory")).toBeNull();
    },
  );

  it("gate2_returned で止まったジョブは awaiting_approval になり、答えの再開で running に戻る", async () => {
    const { loop_id } = await startLoop("l1", ["25 Factory"]);
    const runId = (await loopOf(loop_id)).currentRunId!;
    await finishEcs(runId, { result: "gate", summary: "" }, [["loop.stopped", { loop: "l1", condition: "gate2_returned", detail: "{}", pending: [] }]]);
    expect((await loopOf(loop_id)).state).toBe("awaiting_approval");
    await h.control.requestResume({ sprintRunId: runId, approvalRequestId: "ar-1" });
    expect((await loopOf(loop_id)).state).toBe("running");
    expect(instructionOf((await runOf(runId)).currentJobId!)).toMatchObject({ START: "resume", LOOP_NAME: "l1", APPROVAL_REQUEST_ID: "ar-1" });
  });

  it("loop.stopped の無いまま失敗したジョブのループは run_failed で止まる", async () => {
    const { loop_id } = await startLoop("l1", ["25 Factory"]);
    await finishEcs((await loopOf(loop_id)).currentRunId!, { result: "failed", summary: "" });
    expect(await loopOf(loop_id)).toMatchObject({ state: "stopped", stopCondition: "run_failed" });
  });
});

describe("実行の枠と待ち（受け入れ条件 3、I46）", () => {
  beforeEach(() => setup());

  it("枠が満ちたらループは between_laps（slots）で待ち、枠を使わない。別の実行が終わると次の周回が起きる", async () => {
    const free = accountContext({ plan: "free" });
    const other = await manual([4], OTHER_REPO, free);
    const { loop_id, state } = await startLoop("l1", ["25 Factory"], free);
    expect(state).toBe("between_laps");
    expect(await loopOf(loop_id)).toMatchObject({ waitReason: "slots", currentRunId: null });
    expect((await h.table.getSlots("acc-1", REPO)).account).toBe(1);
    await finishEcs(other.sprint_run_id, { result: "done", summary: "" });
    expect(await loopOf(loop_id)).toMatchObject({ state: "running", waitReason: null, lapRuns: 1 });
    const active = [...h.table.runs.values()].filter((r) => !["done", "stopped", "failed", "cancelled"].includes(r.state)).length;
    expect((await h.table.getSlots("acc-1", REPO)).account).toBe(active);
  });

  it("枠待ちのループを見回りが起こし直しても、GitHub を呼ばずに断る", async () => {
    const free = accountContext({ plan: "free" });
    await manual([4], OTHER_REPO, free);
    const { loop_id } = await startLoop("l1", ["25 Factory"], free);
    const name = h.github.repositoryName.bind(h.github);
    let calls = 0;
    h.github.repositoryName = async (i, r) => {
      calls += 1;
      return name(i, r);
    };
    await loops.sweepLoops();
    expect(calls).toBe(0);
    expect(await loopOf(loop_id)).toMatchObject({ state: "between_laps", waitReason: "slots" });
  });

  it("ループの詳細はそのループの実行だけを返す", async () => {
    const other = await manual([4], OTHER_REPO);
    const { loop_id } = await startLoop("l1", ["25 Factory"]);
    const detail = await loops.getLoopDetail(accountContext(), loop_id);
    expect(detail.runs.map((r) => r.sprint_run_id)).toEqual([(await loopOf(loop_id)).currentRunId]);
    expect(detail.runs.map((r) => r.sprint_run_id)).not.toContain(other.sprint_run_id);
  });

  it("2 つの呼び手が同時に周回を起こしても、実行は 1 本だけ（I44）", async () => {
    const free = accountContext({ plan: "free" });
    const other = await manual([4], OTHER_REPO, free);
    const { loop_id } = await startLoop("l1", ["25 Factory"], free);
    await finishEcs(other.sprint_run_id, { result: "done", summary: "" }).catch(() => undefined);
    await Promise.all([loops.startNextLap("acc-1", loop_id, false), loops.startNextLap("acc-1", loop_id, false), loops.sweepLoops()]);
    expect([...h.table.runs.values()].filter((r) => r.loopId === loop_id)).toHaveLength(1);
  });
});

describe("マイルストーンと課題の取り合い（受け入れ条件 4・5、I45・I47）", () => {
  beforeEach(() => setup());

  it("重なるマイルストーンの 2 本目は 409（題と相手の名前）、重ならなければ始められる", async () => {
    await startLoop("lfactory2", ["25 Factory", "26 根本"]);
    const details = await detailsOf(startLoop("lroot", ["26 根本", "27 x"]));
    expect(details).toEqual({ milestones: ["26 根本"], loop_name: "lfactory2" });
    expect((await startLoop("lflow", ["27 x"])).state).toBe("running");
  });

  it("同時の 2 つの開始で片方だけが通る", async () => {
    const results = await Promise.allSettled([startLoop("a", ["25 Factory"]), startLoop("b", ["25 Factory"])]);
    expect(results.filter((r) => r.status === "fulfilled")).toHaveLength(1);
  });

  it("動いている実行の課題を指す手動の開始は issue_in_use、ループのマイルストーンの課題は issue_in_loop", async () => {
    await manual([4]);
    const used = manual([4, 5]);
    expect(await detailsOf(used)).toEqual({ numbers: [4] });
    await startLoop("lfactory2", ["25 Factory"]);
    expect(await detailsOf(manual([1]))).toEqual({ numbers: [1], loop_name: "lfactory2" });
    expect(await codeOf(manual([2]))).toBe("issue_in_loop");
  });

  it("ループの周回の指示の EXCLUDE_ISSUES に同じリポジトリの手動の実行の課題が並ぶ", async () => {
    await manual([4, 5]);
    const { loop_id } = await startLoop("l1", ["26 根本"]);
    expect(instructionOf((await runOf((await loopOf(loop_id)).currentRunId!)).currentJobId!).EXCLUDE_ISSUES).toBe("4,5");
  });

  it("実行が終わると課題の持ち分が消え、同じ課題で始められる", async () => {
    const a = await manual([4]);
    await finishEcs(a.sprint_run_id, { result: "done", summary: "" });
    expect(await codeOf(manual([4]))).toBe("ok");
  });
});

describe("版（受け入れ条件 6、I48）と移行", () => {
  it("下限が設定に無いと同じリポジトリの 2 本目が断られ、ループは legacy_version で待つ", async () => {
    await setup({ parallelMinVersion: null });
    await manual([4]);
    expect(await codeOf(manual([5]))).toBe("parallel_unsupported_version");
    const { loop_id } = await startLoop("l1", ["25 Factory"]);
    expect(await loopOf(loop_id)).toMatchObject({ state: "between_laps", waitReason: "legacy_version" });
    expect(await codeOf(manual([4], OTHER_REPO))).toBe("ok");
  });

  it("下限以上の版どうしは並び、下限より古い版の実行があれば 2 本目は断られる", async () => {
    await setup({ parallelMinVersion: "10.26.0" });
    await manual([4]);
    expect(await codeOf(manual([5]))).toBe("ok");
    await setup({ parallelMinVersion: "10.27.0" });
    await manual([4]);
    expect(await codeOf(manual([5]))).toBe("parallel_unsupported_version");
  });

  it("移行の済みの行が無ければ、下限以上の版でも 2 本目は断られる", async () => {
    await setup({ parallelMinVersion: "10.0.0" }, false);
    await manual([4]);
    expect(await codeOf(manual([5]))).toBe("parallel_unsupported_version");
  });

  it("埋め戻しは既存の実行を古い版と課題の持ち分へ写し、2 回流しても数が増えず、終わった実行で負にならない", async () => {
    await setup({ parallelMinVersion: "10.0.0" }, false);
    const now = h.clock.now.toISOString();
    const old = (id: string, issues: number[], repositoryId = REPO, state: "running" | "done" = "running") => ({
      sprintRunId: id,
      accountId: "acc-1",
      repositoryId,
      repo: "org-a/repo-a",
      state,
      issues,
      ndfVersion: "10.20.0",
      executionMode: "byok" as const,
      modelCredentialSecretArn: null,
      currentJobId: null,
      jobCount: 1,
      cancelRequested: false,
      failureReason: null,
      createdBy: "user-1",
      createdAt: now,
      updatedAt: now,
    });
    // #2347 より前に作られた実行（legacy の欄が無い）
    h.table.runs.set("old1", old("old1", [4]));
    h.table.runs.set("old2", old("old2", [5], OTHER_REPO));
    h.table.repositorySlots.set("acc-1#101", 1);
    h.table.repositorySlots.set("acc-1#102", 1);
    h.table.accountSlots.set("acc-1", 2);
    expect(await backfill(h.table)).toEqual({ backfilled: 2, migrated: true });
    expect(await backfill(h.table)).toEqual({ backfilled: 0, migrated: true });
    expect((await h.table.getSlots("acc-1", REPO)).legacy).toBe(1);
    expect(await codeOf(manual([4]))).toBe("parallel_unsupported_version");
    // 古い版の実行が終わると legacyCount が 0 に戻り、新しい版の 2 本目が並ぶ
    await h.table.finishRun("old1", "done", null, now);
    expect((await h.table.getSlots("acc-1", REPO)).legacy).toBeUndefined();
    expect(await codeOf(manual([4]))).toBe("ok");
    expect(await codeOf(manual([5]))).toBe("ok");
  });

  it("埋め戻しの後、既存の実行の課題を指す開始は issue_in_use", async () => {
    await setup({ parallelMinVersion: "10.0.0" }, false);
    const now = h.clock.now.toISOString();
    h.table.runs.set("old1", {
      sprintRunId: "old1",
      accountId: "acc-1",
      repositoryId: OTHER_REPO,
      repo: "org-a/repo-b",
      state: "running",
      issues: [4],
      ndfVersion: "10.20.0",
      executionMode: "byok",
      modelCredentialSecretArn: null,
      currentJobId: null,
      jobCount: 1,
      cancelRequested: false,
      failureReason: null,
      createdBy: "user-1",
      createdAt: now,
      updatedAt: now,
    });
    await backfill(h.table);
    expect(await h.table.listIssueShares("acc-1", OTHER_REPO)).toEqual([{ number: 4, sprintRunId: "old1" }]);
  });
});

describe("リポジトリの同時の数（受け入れ条件 8、I49）", () => {
  beforeEach(() => setup());

  it("プランの値 3 で宣言 1 のリポジトリは 2 本目が concurrent_repo_runs で断られる", async () => {
    const pro = accountContext({ plan: "pro" });
    await h.table.setMaxConcurrentRuns("acc-1", REPO, 1);
    await manual([4], REPO, pro);
    expect(await detailsOf(manual([5], REPO, pro))).toEqual({ reasons: [{ quantity: "concurrent_repo_runs", limit: 1, current: 1 }] });
    expect(await codeOf(manual([5], OTHER_REPO, pro))).toBe("ok");
  });
});

describe("手元のランナーの同時のジョブの数（受け入れ条件 9、I50）", () => {
  beforeEach(() => setup());

  it("max_jobs 1 のランナーへ 2 本目を渡さず、1 本目が終わると渡す", async () => {
    const local = accountContext({ executionMode: "local" });
    await manual([4], REPO, local);
    await manual([5], OTHER_REPO, local);
    const runner = { runnerId: "r".repeat(32), maxJobs: 1 };
    const first = await h.control.claimLocalRunJob("acc-1", runner);
    expect(first).not.toBeNull();
    expect(await h.control.claimLocalRunJob("acc-1", runner)).toBeNull();
    expect(h.table.runnerSlots.get(`acc-1#${runner.runnerId}`)).toBe(1);
    await event(first!.instruction.SPRINT_RUN_ID, first!.instruction.JOB_ID, "job.finished", { result: "done", summary: "" });
    await h.control.reportLocalRunJobEnded(first!.instruction.JOB_ID);
    expect(h.table.runnerSlots.get(`acc-1#${runner.runnerId}`)).toBe(0);
    expect(await h.control.claimLocalRunJob("acc-1", runner)).not.toBeNull();
  });
});

describe("人の判断待ちと答え（受け入れ条件 12）", () => {
  beforeEach(() => setup());

  it("人の判断待ちに答えると次のジョブの LOOP_ANSWERS に載り、無い番号は 409 not_pending", async () => {
    const { loop_id } = await startLoop("lfactory2", ["25 Factory"]);
    const runId = (await loopOf(loop_id)).currentRunId!;
    const pending = [{ number: 2, reason: "MVV の外の疑い", lap: 1 }];
    await finishEcs(runId, { result: "done", summary: "" }, [["loop.lap_closed", lap(1, { pending })]]);
    // 周回の後は次の周回が起きている。答えは次に作るジョブに載る
    const detail = await loops.getLoopDetail(accountContext(), loop_id);
    expect(detail.pending).toEqual([{ number: 2, reason: "MVV の外の疑い", lap: 1, answer: null }]);
    expect(detail.laps).toHaveLength(1);
    expect(await codeOf(loops.answer(accountContext(), loop_id, { issue: 9, decision: "approved" }))).toBe("not_pending");
    const out = await loops.answer(accountContext(), loop_id, { issue: 2, decision: "approved", pr: 40 });
    expect(out.pending[0]!.answer).toEqual({ decision: "approved", pr: 40 });
    const second = (await loopOf(loop_id)).currentRunId!;
    await finishEcs(second, { result: "done", summary: "" }, [["loop.lap_closed", lap(2, { pending })]]);
    const third = (await loopOf(loop_id)).currentRunId!;
    expect(instructionOf((await runOf(third)).currentJobId!).LOOP_ANSWERS).toBe("2=approved#40");
  });

  it("別の課題へ同時に答えても、どちらの答えも残る", async () => {
    const { loop_id } = await startLoop("lfactory2", ["25 Factory"]);
    const pending = [
      { number: 2, reason: "a", lap: 1 },
      { number: 3, reason: "b", lap: 1 },
    ];
    await finishEcs((await loopOf(loop_id)).currentRunId!, { result: "done", summary: "" }, [["loop.lap_closed", lap(1, { pending })]]);
    await Promise.all([
      loops.answer(accountContext(), loop_id, { issue: 2, decision: "approved" }),
      loops.answer(accountContext(), loop_id, { issue: 3, decision: "rejected" }),
    ]);
    expect((await loopOf(loop_id)).answers.map((a) => a.number).sort()).toEqual([2, 3]);
  });
});

describe("local の次の周回（受け入れ条件 14、I52）", () => {
  it("local の周回の後は user_start で待ち、見回りは起こさず、next-lap で起きる", async () => {
    await setup();
    const local = accountContext({ executionMode: "local" });
    const { loop_id, state } = await startLoop("l1", ["25 Factory"], local);
    expect(state).toBe("running");
    const claimed = (await h.control.claimLocalRunJob("acc-1"))!;
    await event(claimed.instruction.SPRINT_RUN_ID, claimed.instruction.JOB_ID, "job.finished", { result: "done", summary: "" });
    await h.control.reportLocalRunJobEnded(claimed.instruction.JOB_ID);
    expect(await loopOf(loop_id)).toMatchObject({ state: "between_laps", waitReason: "user_start" });
    await h.control.sweep();
    expect((await loopOf(loop_id)).lapRuns).toBe(1);
    expect(await codeOf(loops.requestNextLap(accountContext({ executionMode: "local" }), loop_id))).toBe("ok");
    expect(await loopOf(loop_id)).toMatchObject({ state: "running", lapRuns: 2 });
    expect(await codeOf(loops.requestNextLap(local, loop_id))).toBe("not_awaiting_user_start");
  });

  it("LOCAL_LOOP_AUTOSTART が真なら待たない。managed・byok も待たない", async () => {
    await setup({ localLoopAutostart: true });
    const { loop_id } = await startLoop("l1", ["25 Factory"], accountContext({ executionMode: "local" }));
    const claimed = (await h.control.claimLocalRunJob("acc-1"))!;
    await event(claimed.instruction.SPRINT_RUN_ID, claimed.instruction.JOB_ID, "job.finished", { result: "done", summary: "" });
    await h.control.reportLocalRunJobEnded(claimed.instruction.JOB_ID);
    expect(await loopOf(loop_id)).toMatchObject({ state: "running", lapRuns: 2 });
  });
});

describe("利用上限の後の再開（受け入れ条件 18・19、I54）", () => {
  beforeEach(() => setup());

  async function localRun(issues: number[]) {
    await manual(issues, REPO, accountContext({ executionMode: "local" }));
    return (await h.control.claimLocalRunJob("acc-1"))!.instruction;
  }

  it("local は awaiting_reset になり、時刻の前の見回りでは何もせず、後で承認の依頼の識別子の無い resume を積む", async () => {
    const ins = await localRun([4]);
    const resetsAt = new Date(h.clock.now.getTime() + 3600_000).toISOString();
    await event(ins.SPRINT_RUN_ID, ins.JOB_ID, "job.finished", { result: "stopped", summary: "", reason: "usage_limit", resets_at: resetsAt });
    await h.control.reportLocalRunJobEnded(ins.JOB_ID);
    expect(await runOf(ins.SPRINT_RUN_ID)).toMatchObject({ state: "awaiting_reset", resumeAt: resetsAt, finishReason: "usage_limit", currentJobId: null });
    expect((await h.table.getSlots("acc-1", REPO)).account).toBe(1);
    await h.control.sweep();
    expect((await runOf(ins.SPRINT_RUN_ID)).state).toBe("awaiting_reset");
    h.clock.now = new Date(Date.parse(resetsAt) + 1000);
    await h.control.sweep();
    expect((await runOf(ins.SPRINT_RUN_ID)).state).toBe("starting");
    const next = (await h.control.claimLocalRunJob("acc-1"))!.instruction;
    expect(next.START).toBe("resume");
    expect(next.APPROVAL_REQUEST_ID).toBeUndefined();
    expect(validateRunJobInstruction(next).ok).toBe(true);
  });

  it("リセット時刻が読めなければ 900 秒後に再開する", async () => {
    const ins = await localRun([4]);
    await event(ins.SPRINT_RUN_ID, ins.JOB_ID, "job.finished", { result: "stopped", summary: "", reason: "usage_limit", resets_at: null });
    await h.control.reportLocalRunJobEnded(ins.JOB_ID);
    expect((await runOf(ins.SPRINT_RUN_ID)).resumeAt).toBe(new Date(h.clock.now.getTime() + 900_000).toISOString());
  });

  it("managed・byok の usage_limit は今の失敗の扱い", async () => {
    const run = await manual([4]);
    await finishEcs(run.sprint_run_id, { result: "stopped", summary: "", reason: "usage_limit", resets_at: null });
    expect(await runOf(run.sprint_run_id)).toMatchObject({ state: "failed", failureReason: "job_failed" });
  });

  it("自動の再開の前に、始めた利用者が席を持たなければ再開せず seat_missing で終える", async () => {
    const ins = await localRun([4]);
    await event(ins.SPRINT_RUN_ID, ins.JOB_ID, "job.finished", { result: "stopped", summary: "", reason: "usage_limit", resets_at: null });
    await h.control.reportLocalRunJobEnded(ins.JOB_ID);
    (h.control as unknown as { deps: { resolveContext: () => Promise<null> } }).deps.resolveContext = async () => null;
    h.clock.now = new Date(h.clock.now.getTime() + 901_000);
    await h.control.sweep();
    expect(await runOf(ins.SPRINT_RUN_ID)).toMatchObject({ state: "failed", failureReason: "seat_missing" });
  });

  it("区間の上限で止まったジョブは stopped と finishReason（section_limit）", async () => {
    const run = await manual([4]);
    await finishEcs(run.sprint_run_id, { result: "stopped", summary: "", reason: "section_limit" });
    expect(await runOf(run.sprint_run_id)).toMatchObject({ state: "stopped", finishReason: "section_limit" });
  });
});

describe("文脈の引き直し", () => {
  it("始めた利用者が席を外されたループは周回を起こさず launch_refused で止まる", async () => {
    await setup();
    loops = new LoopControl({ table: h.table, control: h.control, reader: memoryProgressReader(h.progress), resolveContext: async () => null, log: () => undefined });
    const { loop_id, state } = await startLoop("l1", ["25 Factory"]);
    expect(state).toBe("stopped");
    expect(await loopOf(loop_id)).toMatchObject({ stopCondition: "launch_refused" });
  });
});

describe("選定の規則を持たない（受け入れ条件 1）", () => {
  it("factory/api/src に課題の順位やマイルストーンの候補の選び方の語が無い", () => {
    const dir = new URL("./", import.meta.url);
    const sources = readdirSync(dir).filter((f) => f.endsWith(".ts") && !f.endsWith(".test.ts"));
    for (const f of sources) {
      const text = readFileSync(new URL(f, dir), "utf8");
      expect(text).not.toMatch(/upkeep|rank_digest|max_issues|candidates_of/);
    }
  });
});
