import { beforeEach, describe, expect, it } from "vitest";
import { API_ROLE_ARN, createTestIngest, sign, WEBHOOK_SECRET, type TestIngest } from "../testing/ingest.ts";
import { createIngestApp, type IngestDeps, type IngestLogEntry } from "./app.ts";
import { MemoryProgressEventStore } from "./memory-store.ts";
import { MAX_EVENT_BYTES } from "./validate.ts";

let t: TestIngest;
beforeEach(async () => {
  t = await createTestIngest();
});

const AT = "2026-10-30T01:02:03.4Z";

function runnerEvent(patch: Record<string, unknown> = {}) {
  return {
    sprint_run_id: "sr-1",
    idempotency_key: "job-1:1",
    type: "job.started",
    at: AT,
    source: "runner",
    job_id: "job-1",
    schema_version: 1,
    payload: { ndf_version: "10.26.0", start: "new" },
    ...patch,
  };
}

function answeredEvent(patch: Record<string, unknown> = {}, payload: Record<string, unknown> = {}) {
  return {
    sprint_run_id: "sr-1",
    idempotency_key: "api:ar-1",
    type: "gate.answered",
    at: AT,
    source: "api",
    job_id: null,
    schema_version: 1,
    payload: { approval_request_id: "ar-1", kind: "gate1", decision: "approved", answered_by_account_id: "acc-1", ...payload },
    ...patch,
  };
}

const saveAnswer = () =>
  t.answers.set("ar-1", {
    approvalRequestId: "ar-1",
    sprintRunId: "sr-1",
    kind: "gate1",
    decision: "approved",
    answeredByAccountId: "acc-1",
  });

const UNAUTHORIZED = { error: { code: "unauthorized", message: "認証できません" } };
const FORBIDDEN = { error: { code: "forbidden", message: "この事象を書く権限がありません" } };
const UNAVAILABLE = { error: { code: "ingest_unavailable", message: "時間をおいて送り直してください" } };

async function expectRejected(res: Response, status: number, body: unknown) {
  expect(res.status).toBe(status);
  expect(await res.json()).toEqual(body);
  expect(t.store.events).toHaveLength(0);
}

describe("runner の認証（受け入れ条件 4・18）", () => {
  it("token が無い・Bearer でないと 401 で保存しない", async () => {
    await expectRejected(await t.runner(runnerEvent(), { authorization: null }), 401, UNAUTHORIZED);
    await expectRejected(await t.runner(runnerEvent(), { authorization: "Basic abc" }), 401, UNAUTHORIZED);
  });

  it("形が違う・署名が合わない・期限切れ・知らない鍵の token はどれも 401 で、本文は同じ（I30）", async () => {
    const claims = { jobId: "job-1", sprintRunId: "sr-1" };
    const tokens = [
      "not-a-token",
      await t.tokens.otherKeySign(claims),
      await t.tokens.sign({ ...claims, expiresAt: new Date(Date.now() - 60_000) }),
      await t.tokens.sign(claims, { kid: "retired" }),
    ];
    for (const token of tokens) {
      await expectRejected(await t.runner(runnerEvent(), { authorization: `Bearer ${token}` }), 401, UNAUTHORIZED);
    }
  });
});

describe("runner の照合（受け入れ条件 5・6・7、I26・I27）", () => {
  it("token の実行ジョブと job_id か sprint_run_id のどちらかが違えば 403", async () => {
    const bearer = `Bearer ${await t.tokens.sign({ jobId: "job-1", sprintRunId: "sr-1" })}`;
    await expectRejected(await t.runner(runnerEvent({ job_id: "job-2", idempotency_key: "job-2:1" }), { authorization: bearer }), 403, FORBIDDEN);
    await expectRejected(await t.runner(runnerEvent({ sprint_run_id: "sr-2" }), { authorization: bearer }), 403, FORBIDDEN);
  });

  it.each([
    ["gate.answered", { approval_request_id: "ar-1", kind: "decision", answered_by_account_id: "acc-1" }],
    ["pr.updated", { repo: "o/r", number: 1, state: "open" }],
    ["ci.updated", { repo: "o/r", pr_number: 1, name: "c", conclusion: null }],
  ])("%s を送れば 403", async (type, payload) => {
    await expectRejected(await t.runner(runnerEvent({ type, payload })), 403, FORBIDDEN);
  });

  it.each([
    ["別のジョブの頭", "job-2:1", "job.started"],
    ["seq が 0", "job-1:0", "job.started"],
    ["seq が数でない", "job-1:abc", "job.started"],
    ["job.finished に seq", "job-1:3", "job.finished"],
    ["ほかの種類に finished", "job-1:finished", "job.started"],
  ])("冪等の鍵が %s なら 403", async (_, key, type) => {
    const payload = type === "job.finished" ? { result: "done", summary: "s" } : { ndf_version: "10.26.0", start: "new" };
    await expectRejected(await t.runner(runnerEvent({ idempotency_key: key, type, payload })), 403, FORBIDDEN);
  });

  it("job.finished は <job_id>:finished で残る", async () => {
    const res = await t.runner(runnerEvent({ idempotency_key: "job-1:finished", type: "job.finished", payload: { result: "gate", summary: "s" } }));
    expect(res.status).toBe(202);
    expect(t.store.events).toHaveLength(1);
  });
});

describe("api の認証と照合（受け入れ条件 8・9・10、I28）", () => {
  beforeEach(saveAnswer);

  it("保存済みの答えとすべて一致すれば 202 で残る", async () => {
    const res = await t.api(answeredEvent());
    expect(res.status).toBe(202);
    expect(t.store.events.map((e) => e.idempotency_key)).toEqual(["api:ar-1"]);
  });

  it("IAM の文脈が無い（利用者の Cognito の token だけ）と 401", async () => {
    const res = await t.app.request("/ingest/api/events", {
      method: "POST",
      headers: { Authorization: "Bearer cognito-access-token", "Content-Type": "application/json" },
      body: JSON.stringify(answeredEvent()),
    });
    await expectRejected(res, 401, UNAUTHORIZED);
  });

  it("別のロール・別のアカウント・ロールでない呼び出し元は 403", async () => {
    for (const callerArn of [
      "arn:aws:sts::123456789012:assumed-role/other-role/s",
      "arn:aws:sts::999999999999:assumed-role/approval-api/s",
      "arn:aws:iam::123456789012:user/approval-api",
    ]) {
      await expectRejected(await t.api(answeredEvent(), { callerArn }), 403, FORBIDDEN);
    }
  });

  it("gate.answered 以外は 403", async () => {
    await expectRejected(
      await t.api({ ...runnerEvent({ source: "api", job_id: null, idempotency_key: "api:x" }) }),
      403,
      FORBIDDEN,
    );
  });

  it.each([
    ["依頼の識別子", {}, { approval_request_id: "ar-2" }],
    ["スプリントの実行", { sprint_run_id: "sr-2" }, {}],
    ["種類", {}, { kind: "gate2" }],
    ["答えた人", {}, { answered_by_account_id: "acc-2" }],
    ["承認か差し戻しか", {}, { decision: "rejected" }],
  ])("%s が保存済みの答えと違えば 403", async (_, patch, payload) => {
    const body = answeredEvent(patch, payload);
    if ((payload as { approval_request_id?: string }).approval_request_id === "ar-2") {
      t.answers.set("ar-2", { ...t.answers.get("ar-1")!, approvalRequestId: "ar-1" });
      body.idempotency_key = "api:ar-2";
    }
    await expectRejected(await t.api(body), 403, FORBIDDEN);
  });

  it("答えが未保存なら 403", async () => {
    t.answers.clear();
    await expectRejected(await t.api(answeredEvent()), 403, FORBIDDEN);
  });

  it("冪等の鍵が api:<依頼の識別子> でなければ 403、job_id があれば 403", async () => {
    await expectRejected(await t.api(answeredEvent({ idempotency_key: "api:ar-9" })), 403, FORBIDDEN);
    await expectRejected(await t.api(answeredEvent({ job_id: "job-1" })), 403, FORBIDDEN);
  });

  it("保存済みの答えを読めなければ 503 で保存しない", async () => {
    t.failAnswers.next = true;
    await expectRejected(await t.api(answeredEvent()), 503, UNAVAILABLE);
  });
});

describe("出所と形の検査（受け入れ条件 11・13、I25・I29）", () => {
  it("runner の経路で source が runner でなければ 403", async () => {
    for (const source of ["api", "github", "control"]) {
      await expectRejected(await t.runner(runnerEvent({ source })), 403, FORBIDDEN);
    }
  });

  it("api の経路で source が api でなければ 403", async () => {
    saveAnswer();
    await expectRejected(await t.api(answeredEvent({ source: "runner" })), 403, FORBIDDEN);
  });

  it.each([
    ["知らない種類", runnerEvent({ type: "job.paused" }), "/type: enum"],
    ["schema_version 2", runnerEvent({ schema_version: 2 }), "/schema_version: const"],
    ["payload の余分な鍵", runnerEvent({ payload: { ndf_version: "1", start: "new", extra: 1 } }), "/payload: additionalProperties"],
    ["共通の列の欠け", (({ at: _, ...rest }) => rest)(runnerEvent()), "/: required"],
  ])("%s は 400 で保存しない", async (_, body, message) => {
    await expectRejected(await t.runner(body), 400, { error: { code: "invalid_event", message } });
  });

  it("JSON でない本文は 400", async () => {
    const res = await t.runner(runnerEvent(), { raw: "{not json" });
    await expectRejected(res, 400, { error: { code: "invalid_event", message: "/: json" } });
  });

  it("256 KiB ちょうどの本文は通り、1 バイト超えると 400", async () => {
    const base = runnerEvent({ type: "gate.reached", payload: { kind: "gate1", pr_url: null, material: "" } });
    const pad = MAX_EVENT_BYTES - Buffer.byteLength(JSON.stringify(base));
    const material = "あ".repeat(Math.floor(pad / 3)) + "a".repeat(pad % 3);
    const exact = runnerEvent({ type: "gate.reached", payload: { kind: "gate1", pr_url: null, material } });
    expect(Buffer.byteLength(JSON.stringify(exact))).toBe(MAX_EVENT_BYTES);
    expect((await t.runner(exact)).status).toBe(202);
    const over = runnerEvent({
      type: "gate.reached",
      idempotency_key: "job-1:2",
      payload: { kind: "gate1", pr_url: null, material: material + "a" },
    });
    await t.runner(over).then(async (res) => {
      expect(res.status).toBe(400);
      expect(t.store.events).toHaveLength(1);
    });
  });
});

describe("追記と冪等（受け入れ条件 14・15、I10）", () => {
  it("受け付けた事象は 202 で、列に 1 件、order_key は <ミリ秒にそろえた at>#<鍵>", async () => {
    const res = await t.runner(runnerEvent());
    expect(res.status).toBe(202);
    expect(await res.json()).toEqual({ accepted: true });
    expect(t.store.listRun("sr-1")).toEqual([{ ...runnerEvent(), order_key: "2026-10-30T01:02:03.400Z#job-1:1" }]);
  });

  it("同じ鍵の 2 回目は中身が違っても 202 で、1 件目を変えない", async () => {
    await t.runner(runnerEvent());
    const res = await t.runner(runnerEvent({ payload: { ndf_version: "9.9.9", start: "resume" } }));
    expect(res.status).toBe(202);
    expect(await res.json()).toEqual({ accepted: true });
    expect(t.store.events).toHaveLength(1);
    expect(t.store.events[0]!.payload).toEqual({ ndf_version: "10.26.0", start: "new" });
  });

  it("同じ鍵を同時に 2 本送っても残るのは 1 件", async () => {
    const [a, b] = await Promise.all([t.runner(runnerEvent()), t.runner(runnerEvent({ at: "2026-10-30T09:00:00Z" }))]);
    expect([a.status, b.status]).toEqual([202, 202]);
    expect(t.store.events).toHaveLength(1);
  });

  it("保存に失敗したら 503 で、送り直すと 1 件になる", async () => {
    t.store.failNext = new Error("TransactionConflict");
    await expectRejected(await t.runner(runnerEvent()), 503, UNAVAILABLE);
    expect((await t.runner(runnerEvent())).status).toBe(202);
    expect(t.store.events).toHaveLength(1);
  });

  it("step.recorded が Pull Request を指すときだけ、小文字にそろえた pr_ref を持つ", async () => {
    await t.runner(runnerEvent({ type: "step.recorded", payload: { step: "pr", exit_code: 0, seconds: 1, summary: "s", pull_request: { repo: "Owner/Name", number: 7 } } }));
    await t.runner(runnerEvent({ idempotency_key: "job-1:2", type: "step.recorded", payload: { step: "impl", exit_code: 0, seconds: 1, summary: "s" } }));
    expect(t.store.events.map((e) => e.pr_ref)).toEqual(["owner/name#7", undefined]);
  });
});

describe("GitHub の Webhook（受け入れ条件 12）", () => {
  const prBody = (patch: Record<string, unknown> = {}) => ({
    action: "synchronize",
    repository: { full_name: "owner/name" },
    pull_request: { number: 7, state: "open", merged: false, updated_at: "2026-10-30T05:00:00Z", ...patch },
  });
  const checkBody = (patch: Record<string, unknown> = {}) => ({
    action: "completed",
    repository: { full_name: "owner/name" },
    check_run: {
      name: "factory-check",
      conclusion: "success",
      started_at: "2026-10-30T05:00:00Z",
      completed_at: "2026-10-30T05:03:00Z",
      pull_requests: [{ number: 7 }],
      ...patch,
    },
  });

  // ランナーが Pull Request を作ったときの step.recorded（宛先を引く参照）
  async function linkPr(sprintRunId = "sr-1", jobId = "job-1", repo = "Owner/Name") {
    const res = await t.runner(
      runnerEvent({
        sprint_run_id: sprintRunId,
        job_id: jobId,
        idempotency_key: `${jobId}:1`,
        type: "step.recorded",
        payload: { step: "pr", exit_code: 0, seconds: 1, summary: "s", pull_request: { repo, number: 7 } },
      }),
    );
    expect(res.status).toBe(202);
  }

  const githubEvents = () => t.store.events.filter((e) => e.source === "github");

  it("署名が無い・合わないと 401 で保存しない", async () => {
    await linkPr();
    for (const signature of [null, "sha256=00", sign(JSON.stringify(prBody()), "other-secret"), "sha1=abc"]) {
      const res = await t.github("pull_request", prBody(), { signature });
      expect(res.status).toBe(401);
      expect(await res.json()).toEqual(UNAUTHORIZED);
    }
    expect(githubEvents()).toHaveLength(0);
  });

  it("合った pull_request を pr.updated として、鍵 github:<delivery> と更新時刻の at で残す", async () => {
    await linkPr();
    const res = await t.github("pull_request", prBody({ merged: true, state: "closed" }), { delivery: "abc-123" });
    expect(res.status).toBe(202);
    expect(githubEvents()).toEqual([
      {
        sprint_run_id: "sr-1",
        idempotency_key: "github:abc-123",
        type: "pr.updated",
        at: "2026-10-30T05:00:00Z",
        source: "github",
        job_id: null,
        schema_version: 1,
        payload: { repo: "owner/name", number: 7, state: "merged" },
        order_key: "2026-10-30T05:00:00.000Z#github:abc-123",
      },
    ]);
  });

  it("合った check_run を ci.updated として、completed_at（無ければ started_at）の at で残す", async () => {
    await linkPr();
    await t.github("check_run", checkBody(), { delivery: "d-1" });
    await t.github("check_run", checkBody({ completed_at: null, conclusion: null }), { delivery: "d-2" });
    expect(githubEvents().map((e) => [e.type, e.at, e.payload])).toEqual([
      ["ci.updated", "2026-10-30T05:03:00Z", { repo: "owner/name", pr_number: 7, name: "factory-check", conclusion: "success" }],
      ["ci.updated", "2026-10-30T05:00:00Z", { repo: "owner/name", pr_number: 7, name: "factory-check", conclusion: null }],
    ]);
  });

  it("同じ delivery の 2 回目は 202 で保存しない", async () => {
    await linkPr();
    await t.github("pull_request", prBody(), { delivery: "same" });
    expect((await t.github("pull_request", prBody({ state: "closed" }), { delivery: "same" })).status).toBe(202);
    expect(githubEvents()).toHaveLength(1);
  });

  it("扱わない種類・宛先が引けない・2 つ以上引ける・時刻が無い・形の違う本文は 202 で保存しない", async () => {
    expect((await t.github("pull_request", prBody())).status).toBe(202);
    await linkPr("sr-1", "job-1");
    expect((await t.github("ping", { zen: "z", repository: { full_name: "owner/name" } })).status).toBe(202);
    expect((await t.github("pull_request", prBody({ updated_at: null }), { delivery: "d-2" })).status).toBe(202);
    expect((await t.github("pull_request", {}, { delivery: "d-3" })).status).toBe(202);
    expect((await t.github("pull_request", null, { raw: "{bad", delivery: "d-4" })).status).toBe(202);
    expect((await t.github("pull_request", prBody(), { delivery: null })).status).toBe(202);
    await linkPr("sr-2", "job-2");
    expect((await t.github("pull_request", prBody(), { delivery: "d-5" })).status).toBe(202);
    expect(githubEvents()).toHaveLength(0);
  });

  it("Webhook の秘密を読めなければ 503", async () => {
    t.failSecret.next = true;
    const res = await t.github("pull_request", prBody());
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual(UNAVAILABLE);
  });

  // 現状固定（リファクタリング I-001: translateWebhook の分岐）。期待値の根拠は今の実装の出力
  describe("翻訳の分岐の現状固定", () => {
    async function linkPrNumber(number: number, sprintRunId = "sr-1", jobId = "job-1") {
      const res = await t.runner(
        runnerEvent({
          sprint_run_id: sprintRunId,
          job_id: jobId,
          idempotency_key: `${jobId}:${number}`,
          type: "step.recorded",
          payload: { step: "pr", exit_code: 0, seconds: 1, summary: "s", pull_request: { repo: "owner/name", number } },
        }),
      );
      expect(res.status).toBe(202);
    }

    it("本文が配列・repository の full_name が無いか空なら 202 で保存しない", async () => {
      await linkPr();
      const bodies: unknown[] = [
        [prBody()],
        { ...prBody(), repository: {} },
        { ...prBody(), repository: { full_name: "" } },
        { ...prBody(), repository: null },
      ];
      for (const [i, body] of bodies.entries()) {
        expect((await t.github("pull_request", body, { delivery: `d-${i}` })).status).toBe(202);
      }
      expect(githubEvents()).toHaveLength(0);
    });

    it("pull_request の状態・番号・時刻が扱えない形なら 202 で保存しない", async () => {
      await linkPr();
      const patches: Record<string, unknown>[] = [
        { state: "draft" },
        { state: undefined },
        { number: 0 },
        { number: "7" },
        { number: 7.5 },
        { updated_at: "" },
        { updated_at: 123 },
      ];
      for (const [i, patch] of patches.entries()) {
        expect((await t.github("pull_request", prBody(patch), { delivery: `d-${i}` })).status).toBe(202);
      }
      expect((await t.github("pull_request", { repository: { full_name: "owner/name" }, pull_request: [] }, { delivery: "d-arr" })).status).toBe(202);
      expect(githubEvents()).toHaveLength(0);
    });

    it("merged が true なら state が open でも merged、closed はそのまま closed", async () => {
      await linkPr();
      await t.github("pull_request", prBody({ merged: true, state: "open" }), { delivery: "d-1" });
      await t.github("pull_request", prBody({ state: "closed" }), { delivery: "d-2" });
      await t.github("pull_request", prBody({ merged: "true", state: "open" }), { delivery: "d-3" });
      expect(githubEvents().map((e) => (e.payload as { state: string }).state)).toEqual(["merged", "closed", "open"]);
    });

    it("payload の repo は full_name の大文字を残し、宛先は小文字にそろえた参照で引く", async () => {
      await linkPr();
      await t.github("pull_request", { ...prBody(), repository: { full_name: "OWNER/Name" } }, { delivery: "d-1" });
      await t.github("check_run", { ...checkBody(), repository: { full_name: "OWNER/Name" } }, { delivery: "d-2" });
      expect(githubEvents().map((e) => [e.sprint_run_id, e.idempotency_key, e.payload])).toEqual([
        ["sr-1", "github:d-1", { repo: "OWNER/Name", number: 7, state: "open" }],
        ["sr-1", "github:d-2", { repo: "OWNER/Name", pr_number: 7, name: "factory-check", conclusion: "success" }],
      ]);
    });

    it("check_run の名前・時刻・Pull Request が扱えない形なら 202 で保存しない", async () => {
      await linkPr();
      const patches: Record<string, unknown>[] = [
        { name: "" },
        { name: undefined },
        { completed_at: null, started_at: null },
        { completed_at: "", started_at: "" },
        { pull_requests: [] },
        { pull_requests: undefined },
        { pull_requests: { number: 7 } },
        { pull_requests: [{ number: 0 }, { number: "7" }, null, 7] },
      ];
      for (const [i, patch] of patches.entries()) {
        expect((await t.github("check_run", checkBody(patch), { delivery: `d-${i}` })).status).toBe(202);
      }
      expect((await t.github("check_run", { repository: { full_name: "owner/name" } }, { delivery: "d-none" })).status).toBe(202);
      expect(githubEvents()).toHaveLength(0);
    });

    it("check_run の completed_at が空文字なら started_at を at にする", async () => {
      await linkPr();
      await t.github("check_run", checkBody({ completed_at: "" }), { delivery: "d-1" });
      expect(githubEvents().map((e) => e.at)).toEqual(["2026-10-30T05:00:00Z"]);
    });

    it("conclusion が文字列でない・空文字なら null として残す", async () => {
      await linkPr();
      await t.github("check_run", checkBody({ conclusion: 1 }), { delivery: "d-1" });
      await t.github("check_run", checkBody({ conclusion: "" }), { delivery: "d-2" });
      await t.github("check_run", checkBody({ conclusion: undefined }), { delivery: "d-3" });
      await t.github("check_run", checkBody({ conclusion: "failure" }), { delivery: "d-4" });
      expect(githubEvents().map((e) => (e.payload as { conclusion: string | null }).conclusion)).toEqual([null, null, null, "failure"]);
    });

    it("check_run の Pull Request は不正な番号を除き、宛先の引ける番号で残す", async () => {
      await linkPr();
      await t.github("check_run", checkBody({ pull_requests: [{ number: 9 }, { number: "x" }, { number: 7 }, { number: 7 }] }), { delivery: "d-1" });
      expect(githubEvents().map((e) => [e.sprint_run_id, (e.payload as { pr_number: number }).pr_number])).toEqual([["sr-1", 7]]);
    });

    it("check_run の 2 つの Pull Request が同じ実行を指せば、先に来た番号で 1 件残す", async () => {
      await linkPrNumber(8);
      await linkPrNumber(7);
      await t.github("check_run", checkBody({ pull_requests: [{ number: 8 }, { number: 7 }] }), { delivery: "d-1" });
      expect(githubEvents().map((e) => [e.sprint_run_id, (e.payload as { pr_number: number }).pr_number])).toEqual([["sr-1", 8]]);
    });

    it("check_run の 2 つの Pull Request が別々の実行を指せば 202 で保存しない", async () => {
      await linkPrNumber(7, "sr-1", "job-1");
      await linkPrNumber(8, "sr-2", "job-2");
      expect((await t.github("check_run", checkBody({ pull_requests: [{ number: 7 }, { number: 8 }] }), { delivery: "d-1" })).status).toBe(202);
      expect(githubEvents()).toHaveLength(0);
    });

    it("扱わない種類の Webhook は pull_request の形を持っていても保存しない", async () => {
      await linkPr();
      expect((await t.github("pull_request_review", prBody(), { delivery: "d-1" })).status).toBe(202);
      expect((await t.github("check_suite", checkBody(), { delivery: "d-2" })).status).toBe(202);
      expect(githubEvents()).toHaveLength(0);
    });
  });
});

describe("ログと応答に秘密を出さない（受け入れ条件 19、I30・I31）", () => {
  it("ログは 4 列だけで、token と Webhook の秘密の値は応答にもログにも現れない", async () => {
    const token = await t.tokens.sign({ jobId: "job-1", sprintRunId: "sr-1" });
    const bad = await t.tokens.otherKeySign({ jobId: "job-1", sprintRunId: "sr-1" });
    const bodies = [
      await (await t.runner(runnerEvent(), { authorization: `Bearer ${token}` })).text(),
      await (await t.runner(runnerEvent(), { authorization: `Bearer ${bad}` })).text(),
      await (await t.github("pull_request", { repository: { full_name: "o/r" } }, { signature: "sha256=00" })).text(),
    ];
    const out = JSON.stringify(t.logs) + bodies.join("");
    for (const secret of [token, bad, "test-webhook-secret-value"]) expect(out).not.toContain(secret);
    for (const entry of t.logs) expect(Object.keys(entry).sort()).toEqual(["route", "sprint_run_id", "status", "type"]);
    expect(t.logs[0]).toEqual({ route: "/ingest/events", type: "job.started", status: 202, sprint_run_id: "sr-1" });
  });
});

// 現状固定（リファクタリング I-002: createIngestApp の経路ごとの分岐）。期待値の根拠は今の実装の出力
describe("受け口の経路の分岐の現状固定", () => {
  const BAD_JSON = { error: { code: "invalid_event", message: "/: json" } };

  function appWith(patch: Partial<IngestDeps>) {
    const store = new MemoryProgressEventStore();
    const logs: IngestLogEntry[] = [];
    const deps: IngestDeps = {
      verifier: t.tokens.verify,
      store,
      savedAnswers: async () => null,
      webhookSecret: async () => WEBHOOK_SECRET,
      apiCallerRoleArn: API_ROLE_ARN,
      ...patch,
    };
    return { app: createIngestApp(deps, (e) => logs.push(e)), store, logs };
  }

  const post = (app: ReturnType<typeof createIngestApp>, path: string, raw: string, headers: Record<string, string> = {}) =>
    app.request(path, { method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: raw });

  it("runner の経路は本文の JSON を認証より先に見て、定義の検査は認証の後に見る", async () => {
    await expectRejected(await t.runner(null, { authorization: null, raw: "{bad" }), 400, BAD_JSON);
    await expectRejected(await t.runner(runnerEvent({ type: "job.paused" }), { authorization: null }), 401, UNAUTHORIZED);
  });

  it("api の経路も本文の JSON を認証より先に見て、定義の検査は認証の後に見る", async () => {
    await expectRejected(await t.api(null, { callerArn: null, raw: "{bad" }), 400, BAD_JSON);
    await expectRejected(await t.api(answeredEvent({ type: "job.paused" }), { callerArn: null }), 401, UNAUTHORIZED);
    await expectRejected(
      await t.api(answeredEvent({ type: "job.paused" }), { callerArn: "arn:aws:sts::123456789012:assumed-role/other-role/s" }),
      403,
      FORBIDDEN,
    );
  });

  it("api の呼び出し元の ARN が空文字か文字列でなければ 401", async () => {
    saveAnswer();
    await expectRejected(await t.api(answeredEvent(), { callerArn: "" }), 401, UNAUTHORIZED);
    const res = await t.app.request(
      "/ingest/api/events",
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(answeredEvent()) },
      { event: { requestContext: { authorizer: { iam: { userArn: 123 } } } } } as never,
    );
    await expectRejected(res, 401, UNAUTHORIZED);
  });

  it("Authorization が Bearer と token 1 つの形でなければ 401", async () => {
    const token = await t.tokens.sign({ jobId: "job-1", sprintRunId: "sr-1" });
    for (const authorization of [`Bearer  ${token}`, `Bearer ${token} x`, `bearer ${token}`, "Bearer "]) {
      await expectRejected(await t.runner(runnerEvent(), { authorization }), 401, UNAUTHORIZED);
    }
  });

  it("token の検証が RunJobTokenError 以外で落ちれば 503", async () => {
    const { app, store } = appWith({ verifier: async () => Promise.reject(new Error("鍵を読めない")) });
    const res = await post(app, "/ingest/events", JSON.stringify(runnerEvent()), { Authorization: "Bearer x" });
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual(UNAVAILABLE);
    expect(store.events).toHaveLength(0);
  });

  it("api の経路で保存に失敗したら 503", async () => {
    saveAnswer();
    t.store.failNext = new Error("TransactionConflict");
    await expectRejected(await t.api(answeredEvent()), 503, UNAVAILABLE);
  });

  it("GitHub の経路は署名を本文やヘッダーより先に見る", async () => {
    const res = await t.github("pull_request", null, { raw: "{bad", delivery: null, signature: "sha256=00" });
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual(UNAUTHORIZED);
    const noEvent = await t.app.request("/ingest/github", { method: "POST", headers: { "X-GitHub-Delivery": "d-1", "X-Hub-Signature-256": sign("{}") }, body: "{}" });
    expect(noEvent.status).toBe(202);
    expect(await noEvent.json()).toEqual({ accepted: true });
  });

  it("GitHub の経路で宛先を引けなければ 503、保存に失敗しても 503", async () => {
    await t.runner(
      runnerEvent({ type: "step.recorded", payload: { step: "pr", exit_code: 0, seconds: 1, summary: "s", pull_request: { repo: "owner/name", number: 7 } } }),
    );
    const body = { repository: { full_name: "owner/name" }, pull_request: { number: 7, state: "open", merged: false, updated_at: "2026-10-30T05:00:00Z" } };
    t.store.failNext = new Error("TransactionConflict");
    let res = await t.github("pull_request", body, { delivery: "d-1" });
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual(UNAVAILABLE);

    const { app } = appWith({ store: Object.assign(new MemoryProgressEventStore(), { findSprintRunsByPrRef: async () => Promise.reject(new Error("読めない")) }) });
    const raw = JSON.stringify(body);
    res = await post(app, "/ingest/github", raw, { "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "d-2", "X-Hub-Signature-256": sign(raw) });
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual(UNAVAILABLE);
  });

  it("GitHub の翻訳が定義の検査に通らなければ 202 で保存しない", async () => {
    await t.runner(
      runnerEvent({ type: "step.recorded", payload: { step: "pr", exit_code: 0, seconds: 1, summary: "s", pull_request: { repo: "owner/name", number: 7 } } }),
    );
    const body = { repository: { full_name: "owner/name" }, pull_request: { number: 7, state: "open", merged: false, updated_at: "not-a-time" } };
    expect((await t.github("pull_request", body, { delivery: "d-1" })).status).toBe(202);
    expect(t.store.events.filter((e) => e.source === "github")).toHaveLength(0);
  });

  it("deps を関数で渡すと、作るときには呼ばず要求のときに呼ぶ", async () => {
    let calls = 0;
    const store = new MemoryProgressEventStore();
    const app = createIngestApp(() => {
      calls++;
      return { verifier: t.tokens.verify, store, savedAnswers: async () => null, webhookSecret: async () => WEBHOOK_SECRET, apiCallerRoleArn: API_ROLE_ARN };
    }, () => {});
    expect(calls).toBe(0);
    const token = await t.tokens.sign({ jobId: "job-1", sprintRunId: "sr-1" });
    const res = await post(app, "/ingest/events", JSON.stringify(runnerEvent()), { Authorization: `Bearer ${token}` });
    expect(res.status).toBe(202);
    expect(calls).toBeGreaterThan(0);
    expect(store.events).toHaveLength(1);
  });

  it("ログは事象の種類と実行が決まる前に失敗すれば null、GitHub で保存したときは翻訳後の値を持つ", async () => {
    await t.runner(null, { authorization: null, raw: "{bad" });
    await t.runner(runnerEvent(), { authorization: null });
    await t.runner(runnerEvent({ job_id: "job-2", idempotency_key: "job-2:1" }), { authorization: `Bearer ${await t.tokens.sign({ jobId: "job-1", sprintRunId: "sr-1" })}` });
    await t.runner(
      runnerEvent({ type: "step.recorded", payload: { step: "pr", exit_code: 0, seconds: 1, summary: "s", pull_request: { repo: "owner/name", number: 7 } } }),
    );
    const body = { repository: { full_name: "owner/name" }, pull_request: { number: 7, state: "open", merged: false, updated_at: "2026-10-30T05:00:00Z" } };
    await t.github("pull_request", body, { delivery: "d-1" });
    await t.github("ping", { repository: { full_name: "owner/name" } }, { delivery: "d-2" });
    await t.github("pull_request", body, { delivery: "d-3", signature: null });
    await t.app.request("/ingest/unknown", { method: "POST", body: "{}" });
    expect(t.logs).toEqual([
      { route: "/ingest/events", type: null, status: 400, sprint_run_id: null },
      { route: "/ingest/events", type: null, status: 401, sprint_run_id: null },
      { route: "/ingest/events", type: "job.started", status: 403, sprint_run_id: "sr-1" },
      { route: "/ingest/events", type: "step.recorded", status: 202, sprint_run_id: "sr-1" },
      { route: "/ingest/github", type: "pr.updated", status: 202, sprint_run_id: "sr-1" },
      { route: "/ingest/github", type: null, status: 202, sprint_run_id: null },
      { route: "/ingest/github", type: null, status: 401, sprint_run_id: null },
      { route: "/ingest/unknown", type: null, status: 404, sprint_run_id: null },
    ]);
  });
});

describe("1 件の所要（非機能の性能）", () => {
  it("偽の表へ runner の経路で 1,000 件を送り、token の検証・検査・追記を通した 1 件の平均を測る", async () => {
    const authorization = `Bearer ${await t.tokens.sign({ jobId: "job-1", sprintRunId: "sr-1" })}`;
    const start = performance.now();
    for (let i = 1; i <= 1000; i++) await t.runner(runnerEvent({ idempotency_key: `job-1:${i}` }), { authorization });
    const perEventMs = (performance.now() - start) / 1000;
    console.log(JSON.stringify({ measure: "ingest runner route with memory store", per_event_ms: Number(perEventMs.toFixed(3)) }));
    expect(t.store.events).toHaveLength(1000);
  });
});
