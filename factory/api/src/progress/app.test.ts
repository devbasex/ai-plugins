import { beforeEach, describe, expect, it } from "vitest";
import { createTestIngest, sign, type TestIngest } from "../testing/ingest.ts";
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

describe("step.recorded の Pull Request の照合（#2186 の受け入れ条件 16・17、I37）", () => {
  const prStep = (repo: string, key = "job-1:1") =>
    runnerEvent({ idempotency_key: key, type: "step.recorded", payload: { step: "pr", exit_code: 0, seconds: 1, summary: "s", pull_request: { repo, number: 7 } } });
  const bearer = async () => `Bearer ${await t.tokens.sign({ jobId: "job-1", sprintRunId: "sr-1", repo: "org-a/repo-a" })}`;

  it("token の repo と違うリポジトリの Pull Request は 403 で、pr_ref を残さない", async () => {
    await expectRejected(await t.runner(prStep("org-b/repo-b"), { authorization: await bearer() }), 403, FORBIDDEN);
    expect(t.store.events).toHaveLength(0);
    const webhook = {
      action: "synchronize",
      repository: { full_name: "org-b/repo-b" },
      pull_request: { number: 7, state: "open", merged: false, updated_at: "2026-10-30T05:00:00Z" },
    };
    expect((await t.github("pull_request", webhook)).status).toBe(202);
    expect(t.store.events.filter((e) => e.type === "pr.updated")).toHaveLength(0);
  });

  it("token の repo のものは大文字と小文字を問わず 202 で、pr_ref は小文字", async () => {
    expect((await t.runner(prStep("Org-A/Repo-A"), { authorization: await bearer() })).status).toBe(202);
    expect(t.store.events.map((e) => e.pr_ref)).toEqual(["org-a/repo-a#7"]);
  });

  it("decision.delegated は runner が書ける（#2206）", async () => {
    const res = await t.runner(
      runnerEvent({ type: "decision.delegated", payload: { question: "q", answer: "a", reason: "r", category: null } }),
    );
    expect(res.status).toBe(202);
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
