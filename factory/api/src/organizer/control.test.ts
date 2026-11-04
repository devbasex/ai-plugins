// Factory のオーガナイザー（#2439）の振る舞い。表と進捗の記録はメモリに置き、ランナーの事象は記録へ直に足す
import { beforeEach, describe, expect, it } from "vitest";
import { validateRunJobInstruction } from "@ndf-factory/contracts";
import { ApiError } from "../errors.ts";
import { appendEvent } from "../progress/append.ts";
import { accountContext, createRunsHarness, TEST_CONFIG, type RunsHarness } from "../testing/runs.ts";
import { ResumeRejectedError } from "../runs/control.ts";
import { laneOf } from "./model.ts";

const REPO_ID = 101;
const REPO = "org-a/repo-a";
const CRED_ARN = "arn:aws:secretsmanager:us-east-1:111111111111:secret:ndf-factory/model-credentials/u-1";
let h: RunsHarness;
let n = 0;

beforeEach(async () => {
  h = await createRunsHarness({ ...TEST_CONFIG, parallelMinVersion: "10.0.0" });
  h.table.migrated = true;
  h.github.installations.set(9, new Map([[REPO_ID, REPO]]));
  h.github.openIssues.set(REPO, [1, 2, 3, 4].map((number) => ({ number, title: `t${number}` })));
  h.github.heads.set(`${REPO}#develop`, "aaaa111");
  await h.table.putRegistration({ accountId: "acc-1", repositoryId: REPO_ID, fullName: REPO, installationId: 9, registeredBy: "user-1", registeredAt: "2026-11-01T00:00:00Z" });
  await h.table.putCredential({ accountId: "acc-1", kind: "anthropic_api_key", secretArn: CRED_ARN, updatedAt: "2026-11-01T00:00:00Z" });
});

async function codeOf(p: Promise<unknown>): Promise<string> {
  try {
    await p;
  } catch (err) {
    if (err instanceof ApiError || err instanceof ResumeRejectedError) return err.code;
    throw err;
  }
  return "ok";
}

const at = () => h.clock.now.toISOString();

async function event(sprintRunId: string, jobId: string | null, type: string, payload: Record<string, unknown>, key?: string, source = "runner") {
  await appendEvent(h.progress, {
    sprint_run_id: sprintRunId,
    idempotency_key: key ?? `${jobId}:${++n}`,
    type,
    at: at(),
    source,
    job_id: jobId,
    schema_version: 1,
    payload,
  } as never);
}

async function finish(sprintRunId: string, jobId: string, result: "gate" | "done" | "failed", reason?: string) {
  await event(sprintRunId, jobId, "job.finished", { result, summary: "s", ...(reason ? { reason } : {}) }, `${jobId}:finished`);
  await h.control.onTaskStateChange(jobId, "STOPPED");
}

const runOf = async (id: string) => (await h.table.getRun(id))!;
const startRun = async (issue: number) => (await h.control.startSprintRun(accountContext(), { repositoryId: REPO_ID, issues: [issue] })).sprint_run_id;

// 手動の実行が配布の依頼を出して答えを待つ（I65）
async function requestRelease(sprintRunId: string, prs: number[], requestId = `run-${sprintRunId}-r${++n}`) {
  const run = await runOf(sprintRunId);
  const jobId = run.currentJobId!;
  await event(sprintRunId, jobId, "release.requested", { request_id: requestId, lane: `run-${sprintRunId}`, prs, issues: run.issues, ndf_version: "10.27.16", requested_at: at() }, requestId);
  await finish(sprintRunId, jobId, "gate", "release_requested");
  return requestId;
}

const currentBatch = async () => {
  const root = await h.table.getOrganizer("acc-1", REPO_ID);
  return root?.currentSeq ? h.table.getBatch("acc-1", REPO_ID, root.currentSeq) : null;
};

// 配布の実行のジョブが承認資料をそろえて承認ゲート 2 で止まる（auto なら MVV 判定が通して続ける）
async function present(k: 1 | 2, sha: string, prs: number[], opts: { auto?: boolean } = {}) {
  const batch = (await currentBatch())!;
  const run = await runOf(batch.releaseRunId);
  const jobId = run.currentJobId!;
  await event(run.sprintRunId, jobId, "release.presented", { seq: batch.seq, k, sha, version: "10.28.0", prs, lanes: batch.lanes.map((l) => ({ lane: l.lane, requested: l.requestIds.length > 0 })), added_prs: [], auto: opts.auto ?? false }, `${run.sprintRunId}:presented:${k}`);
  if (opts.auto) return { run, jobId, batch };
  await event(run.sprintRunId, jobId, "gate.reached", { kind: "gate2", pr_url: null, material: "資料" });
  await finish(run.sprintRunId, jobId, "gate");
  return { run, jobId, batch };
}

async function answer(releaseRunId: string, decision: "approved" | "rejected", approvalRequestId = `ar-${++n}`) {
  await event(releaseRunId, null, "gate.answered", { approval_request_id: approvalRequestId, kind: "gate2", decision, answered_by_account_id: "acc-1" }, `api:${approvalRequestId}`, "api");
  return h.control.requestResume({ sprintRunId: releaseRunId, approvalRequestId });
}

async function settle(releaseRunId: string, prs: number[], sha = "aaaa111") {
  const run = await runOf(releaseRunId);
  const jobId = run.currentJobId!;
  const seq = run.releaseSeq!;
  await event(releaseRunId, jobId, "release.progress", { seq, stage: "main_merged" }, `${releaseRunId}:progress:main_merged`);
  await event(releaseRunId, jobId, "release.settled", { seq, result: "released", version: "10.28.0", prod_pr_url: `https://github.com/${REPO}/pull/900`, sha, prs, lanes: [] }, `${releaseRunId}:settled:released`);
  await finish(releaseRunId, jobId, "done");
}

const answeredOf = (sprintRunId: string) => h.progress.listRun(sprintRunId).filter((e) => e.type === "release.answered");
const instructionOf = (jobId: string) => h.platform.taskOf(jobId)![1].instruction;

describe("配布のまとめ（受け入れ条件 1・2・3・5・6、I56・I57・I61・I64）", () => {
  it("同じリポジトリの 2 本の依頼で、まとめた配布 1 つ・配布の実行 1 本・本番の Pull Request 1 本になり、2 本とも同じ答えを持つ", async () => {
    const a = await startRun(1);
    const b = await startRun(2);
    await requestRelease(a, [11]);
    // 1 本目の依頼で、他を待たずに組む（AC 2）
    const batch1 = (await currentBatch())!;
    expect(batch1.seq).toBe(1);
    await requestRelease(b, [12]);
    // 流れている間の依頼は組まずに残る（AC 5・I56）
    expect((await h.table.listBatches("acc-1", REPO_ID, 10)).length).toBe(1);
    const release = await runOf(batch1.releaseRunId);
    expect(release).toMatchObject({ purpose: "release", releaseSeq: 1, state: "starting" });
    expect(release.sprintRunId).not.toBe(a);
    expect(release.sprintRunId).not.toBe(b);
    const instruction = instructionOf(release.currentJobId!);
    expect(validateRunJobInstruction(instruction).ok).toBe(true);
    expect(instruction).toMatchObject({ START: "release", RELEASE_SEQ: "1", RELEASE_STEP: "present", NDF_VERSION: "10.27.16" });

    // 配布の実行の承認資料は、作る時点の develop（2 本の変更を含む）で決まる（AC 3）
    await present(1, "aaaa111", [11, 12]);
    expect((await currentBatch())!.state).toBe("presented");
    const resumed = await answer(release.sprintRunId, "approved");
    expect(instructionOf(resumed.jobId!)).toMatchObject({ START: "release", RELEASE_STEP: "confirm:1" });
    await settle(release.sprintRunId, [11, 12]);

    const batches = await h.table.listBatches("acc-1", REPO_ID, 10);
    expect(batches).toHaveLength(1);
    expect(batches[0]).toMatchObject({ state: "released", result: { version: "10.28.0", prodPrUrl: `https://github.com/${REPO}/pull/900` } });
    for (const id of [a, b]) {
      expect(answeredOf(id).map((e) => e.payload)).toEqual([expect.objectContaining({ seq: 1, result: "released", version: "10.28.0", prod_pr_url: `https://github.com/${REPO}/pull/900` })]);
      // 手動の実行は答えで再開する
      const run = await runOf(id);
      expect(run.state).toBe("starting");
      expect(instructionOf(run.currentJobId!)).toMatchObject({ START: "resume", RELEASE_REQUEST_ID: expect.stringMatching(/^run-/) });
    }
    expect(await runOf(release.sprintRunId)).toMatchObject({ state: "done" });
    expect((await h.table.getOrganizer("acc-1", REPO_ID))!.currentSeq).toBeNull();
  });

  it("配布の実行は実行の枠を持たない（I64）", async () => {
    const a = await startRun(1);
    const before = await h.table.getSlots("acc-1", REPO_ID);
    await requestRelease(a, [11]);
    expect(await h.table.getSlots("acc-1", REPO_ID)).toEqual(before);
    const batch = (await currentBatch())!;
    await present(1, "aaaa111", [11]);
    await answer(batch.releaseRunId, "approved");
    await settle(batch.releaseRunId, [11]);
    expect(await h.table.getSlots("acc-1", REPO_ID)).toEqual(before);
  });

  it("手動の実行は依頼の後 awaiting_approval（release_requested）で枠を持ち、承認の依頼の答えでは再開しない（AC 7・I65）", async () => {
    const a = await startRun(1);
    const id = await requestRelease(a, [11]);
    expect(await runOf(a)).toMatchObject({ state: "awaiting_approval", awaitingReason: "release_requested", releaseRequestId: id });
    expect((await h.table.getSlots("acc-1", REPO_ID)).repository).toBe(1);
    expect(await codeOf(h.control.requestResume({ sprintRunId: a, approvalRequestId: "ar-x" }))).toBe("not_awaiting_approval");
  });

  it("流れている間に届いた依頼は、配布済みの後に次のまとめた配布になる（AC 5）", async () => {
    const a = await startRun(1);
    const b = await startRun(2);
    await requestRelease(a, [11]);
    const first = (await currentBatch())!;
    await present(1, "aaaa111", [11]);
    await requestRelease(b, [12]);
    await answer(first.releaseRunId, "approved");
    await settle(first.releaseRunId, [11]);
    const second = (await currentBatch())!;
    expect(second.seq).toBe(2);
    expect(second.lanes.find((l) => l.requestIds.length > 0)!.sprintRunId).toBe(b);
    expect(answeredOf(b)).toHaveLength(0);
  });

  it("組む書き込みが落ちれば、まとめた配布も配布の実行も無い（AC 6・I57）", async () => {
    const a = await startRun(1);
    h.table.failNextCompose = true;
    await requestRelease(a, [11]);
    expect(await h.table.listBatches("acc-1", REPO_ID, 10)).toHaveLength(0);
    expect([...h.table.runs.values()].filter((r) => r.purpose === "release")).toHaveLength(0);
    // 見回りが続きから組む
    await h.control.sweep();
    expect(await h.table.listBatches("acc-1", REPO_ID, 10)).toHaveLength(1);
    expect([...h.table.runs.values()].filter((r) => r.purpose === "release")).toHaveLength(1);
  });

  it("同時の 2 つの tick で組まれるのは 1 つ（I56）", async () => {
    const a = await startRun(1);
    h.table.failNextCompose = true;
    await requestRelease(a, [11]);
    await Promise.all([h.organizer.tick("acc-1", REPO_ID), h.organizer.tick("acc-1", REPO_ID)]);
    expect(await h.table.listBatches("acc-1", REPO_ID, 10)).toHaveLength(1);
    expect([...h.table.runs.values()].filter((r) => r.purpose === "release")).toHaveLength(1);
  });
});

describe("答えと出し直し（受け入れ条件 4、I60）", () => {
  it("提示 1 への承認で develop が進んでいれば represent、提示 2 への答えでは develop を読まずに confirm:2", async () => {
    const a = await startRun(1);
    await requestRelease(a, [11]);
    const batch = (await currentBatch())!;
    await present(1, "aaaa111", [11]);
    h.github.heads.set(`${REPO}#develop`, "bbbb222");
    const r1 = await answer(batch.releaseRunId, "approved");
    expect(instructionOf(r1.jobId!)).toMatchObject({ RELEASE_STEP: "represent" });
    expect((await currentBatch())!.state).toBe("represented");
    await present(2, "bbbb222", [11, 13]);
    h.github.heads.set(`${REPO}#develop`, "cccc333");
    const reads = h.github.headReads.length;
    const r2 = await answer(batch.releaseRunId, "approved");
    expect(h.github.headReads.length).toBe(reads);
    expect(instructionOf(r2.jobId!)).toMatchObject({ RELEASE_STEP: "confirm:2" });
    expect((await currentBatch())!).toMatchObject({ state: "confirmed", confirmedK: 2 });
    expect((await currentBatch())!.presentations.map((p) => p.sha)).toEqual(["aaaa111", "bbbb222"]);
  });

  it("MVV 判定が通した提示は承認の依頼を作らずに確定する（E12）", async () => {
    const a = await startRun(1);
    await requestRelease(a, [11]);
    const { run } = await present(1, "aaaa111", [11], { auto: true });
    await h.organizer.tick("acc-1", REPO_ID);
    expect((await currentBatch())!).toMatchObject({ state: "confirmed", confirmedK: 1 });
    await settle(run.sprintRunId, [11]);
    expect(answeredOf(a)).toHaveLength(1);
  });

  it("差し戻しはジョブを起こさず、最後の提示の範囲の依頼へ rejected を返す（I61）", async () => {
    const a = await startRun(1);
    const b = await startRun(2);
    await requestRelease(a, [11]);
    const batch = (await currentBatch())!;
    await present(1, "aaaa111", [11]);
    await requestRelease(b, [12]);
    const r = await answer(batch.releaseRunId, "rejected");
    expect(r.jobId).toBeNull();
    expect(await h.table.getBatch("acc-1", REPO_ID, 1)).toMatchObject({ state: "rejected" });
    expect(await runOf(batch.releaseRunId)).toMatchObject({ state: "stopped" });
    expect(answeredOf(a).map((e) => e.payload)).toEqual([expect.objectContaining({ result: "rejected" })]);
    // 範囲に入らない依頼は次のまとめた配布になる
    expect(answeredOf(b)).toHaveLength(0);
    expect((await currentBatch())!.seq).toBe(2);
  });

  it("提示 1 への承認の再送は、提示 2 の承認にしない", async () => {
    const a = await startRun(1);
    await requestRelease(a, [11]);
    const batch = (await currentBatch())!;
    await present(1, "aaaa111", [11]);
    h.github.heads.set(`${REPO}#develop`, "bbbb222");
    await answer(batch.releaseRunId, "approved", "ar-first");
    await present(2, "bbbb222", [11, 13]);
    expect(await codeOf(h.control.requestResume({ sprintRunId: batch.releaseRunId, approvalRequestId: "ar-first" }))).toBe("not_presented");
    expect((await currentBatch())!).toMatchObject({ state: "presented", confirmedK: null });
  });

  it("確定した後に本番のプランの中の判断で止まったら、承認ゲート 2 の答えの再送で本番のプランを流し直さない", async () => {
    const a = await startRun(1);
    await requestRelease(a, [11]);
    const batch = (await currentBatch())!;
    h.github.heads.set(`${REPO}#develop`, "aaaa111");
    await present(1, "aaaa111", [11]);
    const r = await answer(batch.releaseRunId, "approved", "ar-ok");
    expect(instructionOf(r.jobId!)).toMatchObject({ RELEASE_STEP: "confirm:1" });
    const run = await runOf(batch.releaseRunId);
    await event(run.sprintRunId, run.currentJobId, "gate.reached", { kind: "decision", pr_url: null, material: "判断" });
    await finish(run.sprintRunId, run.currentJobId!, "gate");
    expect(await codeOf(h.control.requestResume({ sprintRunId: batch.releaseRunId, approvalRequestId: "ar-ok" }))).toBe("not_presented");
  });

  it("提示の前の判断への答えは、続き（continue）でなく提示のプランから流し直す", async () => {
    const a = await startRun(1);
    await requestRelease(a, [11]);
    const batch = (await currentBatch())!;
    const run = await runOf(batch.releaseRunId);
    await event(run.sprintRunId, run.currentJobId, "gate.reached", { kind: "decision", pr_url: null, material: "判断" });
    await finish(run.sprintRunId, run.currentJobId!, "gate");
    await event(run.sprintRunId, null, "gate.answered", { approval_request_id: "ar-d", kind: "decision", answered_by_account_id: "acc-1" }, "api:ar-d", "api");
    const r = await h.control.requestResume({ sprintRunId: run.sprintRunId, approvalRequestId: "ar-d" });
    expect(instructionOf(r.jobId!)).toMatchObject({ RELEASE_STEP: "present" });
  });
});

describe("待ち（受け入れ条件 8、I62）", () => {
  it("Factory の外の本番の Pull Request が開いている間は組まず、待ちの理由を残し、閉じた後の見回りで組む", async () => {
    h.github.pulls.set(REPO, [{ head: "release/v10.27.9", base: "main", state: "open", url: `https://github.com/${REPO}/pull/77` }]);
    const a = await startRun(1);
    await requestRelease(a, [11]);
    expect(await currentBatch()).toBeNull();
    const list = await h.organizer.list(accountContext(), REPO_ID);
    expect(list.organizer).toEqual({ current_seq: null, wait_reason: "external_release_open", wait_detail: `https://github.com/${REPO}/pull/77` });
    expect((await h.table.listOrgLogs("acc-1", REPO_ID)).map((l) => l.kind)).toEqual(["waited"]);
    h.github.pulls.set(REPO, [{ head: "release/v10.27.9", base: "main", state: "merged", url: `https://github.com/${REPO}/pull/77` }]);
    await h.control.sweep();
    expect((await currentBatch())!.seq).toBe(1);
    expect((await h.table.getOrganizer("acc-1", REPO_ID))!.waitReason).toBeNull();
  });

  it("差し戻したまとめた配布が自分で開いた release/v<版> は外の本番と見なさず、次の配布を組む", async () => {
    const a = await startRun(1);
    await requestRelease(a, [11]);
    const batch = (await currentBatch())!;
    await present(1, "aaaa111", [11]);
    await answer(batch.releaseRunId, "rejected");
    h.github.pulls.set(REPO, [{ head: "release/v10.28.0", base: "main", state: "open", url: `https://github.com/${REPO}/pull/90` }]);
    await requestRelease(await startRun(2), [12]);
    expect((await currentBatch())!.seq).toBe(2);
  });

  it("head が develop の main 宛ての Pull Request でも待つ", async () => {
    h.github.pulls.set(REPO, [{ head: "develop", base: "main", state: "open", url: `https://github.com/${REPO}/pull/78` }]);
    await requestRelease(await startRun(1), [11]);
    expect(await currentBatch()).toBeNull();
  });

  it("依頼を出していない古い版の実行がある間は組まない。依頼を出して待つ古い版の実行は数えない", async () => {
    h.table.migrated = false;
    const a = await startRun(1);
    expect((await runOf(a)).legacy).toBe(true);
    await requestRelease(a, [11]);
    // 古い版の実行は依頼を出して待っているので数えず、組む
    expect((await currentBatch())!.seq).toBe(1);
  });

  it("まとめた配布が流れている間は古い版の実行を始めない", async () => {
    const a = await startRun(1);
    await requestRelease(a, [11]);
    expect(await currentBatch()).not.toBeNull();
    // 依頼を待つ実行を取り消してリポジトリの枠を空け、古い版の実行を始める
    await h.control.cancelSprintRun(accountContext(), a);
    h.table.migrated = false;
    expect(await codeOf(startRun(2))).toBe("parallel_unsupported_version");
  });

  it("照らした後に古い版の実行が数えに入れば、組む書き込みは落ちる（legacySeq）", async () => {
    const a = await startRun(1);
    h.table.failNextCompose = true;
    await requestRelease(a, [11]);
    // 古い版の実行が 1 本入って抜け、legacyCount は 0 に戻るが legacySeq は 1 進む
    h.table.legacySeqs.set(`acc-1#${REPO_ID}`, 1);
    const getSlots = h.table.getSlots.bind(h.table);
    h.table.getSlots = async (acc, repo) => ({ ...(await getSlots(acc, repo)), legacySeq: undefined });
    await h.organizer.tick("acc-1", REPO_ID);
    expect(await h.table.listBatches("acc-1", REPO_ID, 10)).toHaveLength(0);
    h.table.getSlots = getSlots;
    await h.organizer.tick("acc-1", REPO_ID);
    expect(await h.table.listBatches("acc-1", REPO_ID, 10)).toHaveLength(1);
  });
});

describe("止まった配布と再開（受け入れ条件 12・13、I58・I59・I63）", () => {
  it("配布の実行が失敗すると止まった配布になり、main のマージの後は continue だけで、差し戻しは 409", async () => {
    const a = await startRun(1);
    await requestRelease(a, [11]);
    const batch = (await currentBatch())!;
    await present(1, "aaaa111", [11]);
    await answer(batch.releaseRunId, "approved");
    const run = await runOf(batch.releaseRunId);
    await event(run.sprintRunId, run.currentJobId, "release.progress", { seq: 1, stage: "main_merged" }, `${run.sprintRunId}:progress:main_merged`);
    await finish(run.sprintRunId, run.currentJobId!, "failed");
    expect(await runOf(run.sprintRunId)).toMatchObject({ state: "awaiting_approval", awaitingReason: "release_stalled" });
    expect((await currentBatch())!).toMatchObject({ state: "stalled", stalledFrom: "confirmed", mainMerged: true });
    const list = await h.organizer.list(accountContext(), REPO_ID);
    expect(list.items).toEqual([expect.objectContaining({ kind: "release_stalled", release: expect.objectContaining({ choices: ["continue"] }) })]);
    expect(await codeOf(h.organizer.resolve(accountContext(), REPO_ID, 1, { choice: "reject" }))).toBe("release_after_main");
    expect(await h.organizer.resolve(accountContext(), REPO_ID, 1, { choice: "continue" })).toEqual({ seq: 1, state: "confirmed" });
    const resumed = await runOf(run.sprintRunId);
    expect(instructionOf(resumed.currentJobId!)).toMatchObject({ START: "release", RELEASE_STEP: "continue" });
    expect(await codeOf(h.organizer.resolve(accountContext(), REPO_ID, 1, { choice: "continue" }))).toBe("not_stalled");
  });

  it("配布の実行が取り消されても、GitHub で main へマージ済みなら差し戻さない", async () => {
    const a = await startRun(1);
    await requestRelease(a, [11]);
    const batch = (await currentBatch())!;
    await present(1, "aaaa111", [11]);
    await answer(batch.releaseRunId, "approved");
    h.github.pulls.set(REPO, [{ head: "release/v10.28.0", base: "main", state: "merged", url: `https://github.com/${REPO}/pull/91` }]);
    await h.table.finishRun(batch.releaseRunId, "cancelled", null, at());
    await h.organizer.tick("acc-1", REPO_ID);
    expect(await h.table.getBatch("acc-1", REPO_ID, 1)).toMatchObject({ state: "confirmed", mainMerged: true });
    expect(answeredOf(a)).toHaveLength(0);
  });

  it("確定の後に止まり本番に手を付けた跡が無ければ confirm:<k>、GitHub に枝があれば continue", async () => {
    const a = await startRun(1);
    await requestRelease(a, [11]);
    const batch = (await currentBatch())!;
    await present(1, "aaaa111", [11]);
    await answer(batch.releaseRunId, "approved");
    let run = await runOf(batch.releaseRunId);
    await finish(run.sprintRunId, run.currentJobId!, "failed");
    await h.organizer.resolve(accountContext(), REPO_ID, 1, { choice: "continue" });
    run = await runOf(batch.releaseRunId);
    expect(instructionOf(run.currentJobId!)).toMatchObject({ RELEASE_STEP: "confirm:1" });
    await finish(run.sprintRunId, run.currentJobId!, "failed");
    h.github.heads.set(`${REPO}#release/v10.28.0`, "dddd444");
    await h.organizer.resolve(accountContext(), REPO_ID, 1, { choice: "continue" });
    run = await runOf(batch.releaseRunId);
    expect(instructionOf(run.currentJobId!)).toMatchObject({ RELEASE_STEP: "continue" });
  });

  it("提示の前に止まった配布の差し戻しは、受けた依頼すべてへ rejected を返す", async () => {
    const a = await startRun(1);
    await requestRelease(a, [11]);
    const batch = (await currentBatch())!;
    const run = await runOf(batch.releaseRunId);
    await finish(run.sprintRunId, run.currentJobId!, "failed");
    expect(await h.organizer.resolve(accountContext(), REPO_ID, 1, { choice: "reject" })).toEqual({ seq: 1, state: "rejected" });
    expect(answeredOf(a).map((e) => e.payload)).toEqual([expect.objectContaining({ result: "rejected" })]);
  });

  it("答えを書いた後に届け損ねた依頼は、見回りが 1 回だけ届け直し、届け終えたら索引から外れる", async () => {
    const a = await startRun(1);
    await requestRelease(a, [11]);
    const batch = (await currentBatch())!;
    await present(1, "aaaa111", [11]);
    await answer(batch.releaseRunId, "approved");
    // 手動の実行の再開を一度だけ落とす（busy として扱われ、届け終えない）
    const original = h.control.resumeForRelease.bind(h.control);
    h.control.resumeForRelease = async () => "busy";
    await settle(batch.releaseRunId, [11]);
    expect(answeredOf(a)).toHaveLength(1);
    expect((await h.table.listOrgRequests("acc-1", REPO_ID))[0]!.deliveredAt).toBeNull();
    expect(await h.table.listOrganizerActive()).toEqual([{ accountId: "acc-1", repositoryId: REPO_ID }]);
    h.control.resumeForRelease = original;
    await h.control.sweep();
    await h.control.sweep();
    expect(answeredOf(a)).toHaveLength(1);
    expect((await h.table.listOrgRequests("acc-1", REPO_ID))[0]!.deliveredAt).not.toBeNull();
    expect(await h.table.listOrganizerActive()).toEqual([]);
    expect((await runOf(a)).state).toBe("starting");
  });

  it("再開した実行が別の承認ゲートで待っていれば、届け直しは再開せずに届け終える", async () => {
    const a = await startRun(1);
    const id = await requestRelease(a, [11]);
    const batch = (await currentBatch())!;
    await present(1, "aaaa111", [11]);
    await answer(batch.releaseRunId, "approved");
    await settle(batch.releaseRunId, [11]);
    // 再開した手動の実行が別の承認ゲートで止まる
    const run = await runOf(a);
    await finish(a, run.currentJobId!, "gate");
    expect(await runOf(a)).toMatchObject({ state: "awaiting_approval", awaitingReason: null });
    expect(await h.control.resumeForRelease(a, id)).toBe("not_waiting");
    expect((await runOf(a)).currentJobId).toBeNull();
  });

  it("同じ依頼をランナーが再送しても受けた依頼の行は 1 行（I59）", async () => {
    const a = await startRun(1);
    const id = await requestRelease(a, [11]);
    const rows = await h.organizer.newRequestRows(await runOf(a), { requested: [{ request_id: id, lane: `run-${a}`, prs: [11], issues: [1], ndf_version: "10.27.16", requested_at: at() }], presented: [], progress: [], settled: null });
    expect(rows).toEqual([]);
    expect(await h.table.listOrgRequests("acc-1", REPO_ID)).toHaveLength(1);
  });
});

describe("一覧と履歴（受け入れ条件 9・11、I66）", () => {
  it("一覧は同じリポジトリの承認の依頼とループの人の判断待ちを 1 つの並びで返し、別のリポジトリのものを含まない", async () => {
    h.github.installations.get(9)!.set(202, "org-a/repo-b");
    h.github.openIssues.set("org-a/repo-b", [{ number: 1, title: "x" }]);
    await h.table.putRegistration({ accountId: "acc-1", repositoryId: 202, fullName: "org-a/repo-b", installationId: 9, registeredBy: "user-1", registeredAt: "2026-11-01T00:00:00Z" });
    const a = await startRun(1);
    const other = (await h.control.startSprintRun(accountContext(), { repositoryId: 202, issues: [1] })).sprint_run_id;
    for (const id of [a, other]) {
      const run = await runOf(id);
      await event(id, run.currentJobId, "gate.reached", { kind: "gate1", pr_url: `https://github.com/${REPO}/pull/5`, material: "m" });
      await finish(id, run.currentJobId!, "gate");
    }
    const list = await h.organizer.list(accountContext(), REPO_ID);
    expect(list.items).toEqual([expect.objectContaining({ kind: "gate1", sprint_run_id: a, pr_url: `https://github.com/${REPO}/pull/5` })]);
  });

  it("履歴は提示・確定・結果と記録を新しい順に返す", async () => {
    const a = await startRun(1);
    await requestRelease(a, [11]);
    const batch = (await currentBatch())!;
    await present(1, "aaaa111", [11]);
    await answer(batch.releaseRunId, "approved");
    await settle(batch.releaseRunId, [11]);
    const history = await h.organizer.history(accountContext(), REPO_ID, 20);
    expect(history.items).toHaveLength(1);
    expect(history.items[0]).toMatchObject({ seq: 1, state: "released", confirmed_k: 1, result: { version: "10.28.0" } });
    expect(history.items[0]!.log.map((l) => l.kind)).toEqual(["composed", "presented", "confirmed", "released"]);
  });

  it("別のアカウントのリポジトリは 404", async () => {
    expect(await codeOf(h.organizer.list(accountContext({ accountId: "acc-2" }), REPO_ID))).toBe("repository_not_found");
  });
});

describe("laneOf", () => {
  it("ループはループの名前、手動の実行は実行の識別子を切り詰めてレーンの名前にする", () => {
    expect(laneOf({ sprintRunId: "x" }, "LFactory3-very-long-loop-name-over-limit")).toBe("loop-lfactory3-very-long-loop-na");
    expect(laneOf({ sprintRunId: "0123456789ABCDEF0123456789abcdef" }, null)).toBe("run-0123456789abcdef");
    expect(laneOf({ sprintRunId: "sr1" }, null)).toBe("run-sr1");
  });
});
