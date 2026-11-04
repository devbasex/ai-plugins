import { describe, expect, it } from "vitest";
import {
  EVENT_TYPES,
  PHASE_NAMES,
  isPhaseName,
  isIngestResponse,
  progressEventSchema,
  runJobInstructionSchema,
  validateProgressEvent,
  validateLocalRunnerDeclaration,
  validateRunJobInstruction,
} from "../src/index.ts";

// 確定仕様 docs/specifications/ndf-factory-service-boundary.md の「実行ジョブの指示」の入力（11 項目）と、開発ループの 5 項目（#2347）
const SPEC_INSTRUCTION_INPUTS = [
  "SPRINT_RUN_ID",
  "JOB_ID",
  "REPO",
  "NDF_VERSION",
  "START",
  "ISSUES",
  "APPROVAL_REQUEST_ID",
  "INGEST_URL",
  "INGEST_TOKEN_SECRET_ARN",
  "EXECUTION_MODE",
  "MODEL_CREDENTIAL_SECRET_ARN",
  "LOOP_NAME",
  "LOOP_MILESTONES",
  "LOOP_BUDGET",
  "EXCLUDE_ISSUES",
  "LOOP_ANSWERS",
  "RELEASE_SEQ",
  "RELEASE_STEP",
  "RELEASE_REQUEST_ID",
];

// 同じ確定仕様の「進捗の事象の種類と受け口」の種類（13 種類）と、区間と開発ループの 3 種類（#2347）
const SPEC_EVENT_TYPES = [
  "job.started",
  "phase.changed",
  "step.recorded",
  "attention",
  "gate.reached",
  "gate.answered",
  "usage.recorded",
  "job.finished",
  "pr.updated",
  "ci.updated",
  "decision.delegated",
  "issue.requested",
  "issue.request_resolved",
  "section.started",
  "loop.lap_closed",
  "loop.stopped",
  "release.requested",
  "release.presented",
  "release.progress",
  "release.settled",
  "release.answered",
];

// 同じ確定仕様の「進捗の正本」の列から、受け口が作る order_key・pr_ref を除いたもの
const SPEC_COMMON_COLUMNS = ["sprint_run_id", "idempotency_key", "type", "at", "source", "job_id", "schema_version", "payload"];

const PAYLOADS: Record<string, Record<string, unknown>> = {
  "job.started": { ndf_version: "10.26.0", start: "new" },
  "phase.changed": { phase: "implementation", issues: [2171] },
  "step.recorded": { step: "impl", exit_code: 0, seconds: 12.5, summary: "済んだ" },
  attention: { reason: "stalled", summary: "止まった" },
  "gate.reached": { kind: "gate1", pr_url: "https://github.com/o/r/pull/1", material: "資料" },
  "gate.answered": { approval_request_id: "ar-1", kind: "gate1", decision: "approved", answered_by_account_id: "acc-1" },
  "usage.recorded": { model: "claude", input_tokens: 10, output_tokens: 5, cost_usd: 0.01, step: "impl" },
  "job.finished": { result: "done", summary: "終わった" },
  "pr.updated": { repo: "o/r", number: 1, state: "merged" },
  "ci.updated": { repo: "o/r", pr_number: 1, name: "check", conclusion: null },
  "decision.delegated": { question: "どちらにしますか", answer: "A", reason: "既存の方針に沿う", category: null },
  "issue.requested": { repo: "org-b/repo-b", title: "直してほしい", body: "由来: org-a/repo-a#1" },
  "issue.request_resolved": {
    request_key: "job-1:3",
    outcome: "filed",
    issue: { repo: "org-b/repo-b", number: 7, url: "https://github.com/org-b/repo-b/issues/7" },
    duplicate_of: null,
  },
  "section.started": { section: 2, command: "/ndf:development-workflow #2347" },
  "loop.lap_closed": {
    loop: "lfactory2",
    lap: 3,
    issues: [2347],
    result: "完了",
    version: null,
    cost_usd: 1.5,
    seconds: 3600,
    pending: [{ number: 2348, reason: "MVV の外の疑い", lap: 3 }],
  },
  "loop.stopped": { loop: "lfactory2", condition: "budget_usd", detail: '{"values":{"usd":60}}', pending: [] },
  "release.requested": {
    request_id: "loop-l1-r3f9a1c2e4b7d",
    lane: "loop-l1",
    prs: [2440],
    issues: [2439],
    ndf_version: "10.27.16",
    requested_at: "2026-11-02T12:00:00Z",
  },
  "release.presented": {
    seq: 1,
    k: 1,
    sha: "0123abcd",
    version: "10.28.0",
    prs: [2440],
    lanes: [{ lane: "loop-l1", requested: true }],
    added_prs: [],
    auto: false,
  },
  "release.progress": { seq: 1, stage: "main_merged" },
  "release.settled": {
    seq: 1,
    result: "released",
    version: "10.28.0",
    prod_pr_url: "https://github.com/o/r/pull/9",
    sha: "0123abcd",
    prs: [2440],
    lanes: ["loop-l1"],
  },
  "release.answered": { seq: 1, request_id: "loop-l1-r3f9a1c2e4b7d", result: "released", version: "10.28.0" },
};

function event(type: string, patch: Record<string, unknown> = {}) {
  return {
    sprint_run_id: "sr-1",
    idempotency_key: "job-1:1",
    type,
    at: "2026-10-30T01:02:03.456Z",
    source: "runner",
    job_id: "job-1",
    schema_version: 1,
    payload: PAYLOADS[type],
    ...patch,
  };
}

const instruction = (patch: Record<string, string | undefined> = {}) =>
  Object.fromEntries(
    Object.entries({
      SPRINT_RUN_ID: "sr-1",
      JOB_ID: "job-1",
      REPO: "owner/name",
      NDF_VERSION: "10.26.0",
      START: "new",
      ISSUES: "2171,2186",
      INGEST_URL: "https://api.example.com/ingest/events",
      INGEST_TOKEN_SECRET_ARN: "arn:aws:secretsmanager:us-east-1:123456789012:secret:token",
      EXECUTION_MODE: "managed",
      ...patch,
    }).filter(([, v]) => v !== undefined),
  );

describe("実行ジョブの指示の定義（受け入れ条件 1）", () => {
  it("入力の名前が確定仕様の 11 項目と開発ループの 5 項目に 1 対 1 に対応する", () => {
    expect(Object.keys(runJobInstructionSchema.properties).sort()).toEqual([...SPEC_INSTRUCTION_INPUTS].sort());
  });

  it("new・managed の正しい指示は通る", () => {
    expect(validateRunJobInstruction(instruction()).ok).toBe(true);
  });

  it("START が new で ISSUES が無いと落ちる", () => {
    expect(validateRunJobInstruction(instruction({ ISSUES: undefined })).ok).toBe(false);
  });

  it("START が resume なら APPROVAL_REQUEST_ID は任意（無ければ答えを書かずに続ける。#2347 の決定 7）", () => {
    expect(validateRunJobInstruction(instruction({ START: "resume", ISSUES: undefined })).ok).toBe(true);
    expect(validateRunJobInstruction(instruction({ START: "resume", ISSUES: undefined, APPROVAL_REQUEST_ID: "ar-1" })).ok).toBe(true);
  });

  it("byok で MODEL_CREDENTIAL_SECRET_ARN が無いと落ち、あれば通る", () => {
    const arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:model";
    expect(validateRunJobInstruction(instruction({ EXECUTION_MODE: "byok" })).ok).toBe(false);
    expect(validateRunJobInstruction(instruction({ EXECUTION_MODE: "byok", MODEL_CREDENTIAL_SECRET_ARN: arn })).ok).toBe(true);
  });

  it.each(["local", "managed"])("%s に MODEL_CREDENTIAL_SECRET_ARN があると落ちる", (mode) => {
    const arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:model";
    expect(validateRunJobInstruction(instruction({ EXECUTION_MODE: mode, MODEL_CREDENTIAL_SECRET_ARN: arn })).ok).toBe(false);
  });

  it("START が loop は LOOP_NAME と LOOP_MILESTONES を要り、ISSUES を許さない（#2347）", () => {
    const loop = { START: "loop", ISSUES: undefined, LOOP_NAME: "lfactory2", LOOP_MILESTONES: "25 Factory,26 根本" };
    expect(validateRunJobInstruction(instruction(loop)).ok).toBe(true);
    expect(
      validateRunJobInstruction(
        instruction({ ...loop, LOOP_BUDGET: "laps=3,usd=60", EXCLUDE_ISSUES: "2171,2186", LOOP_ANSWERS: "12=approved#40,13=rejected" }),
      ).ok,
    ).toBe(true);
    expect(validateRunJobInstruction(instruction({ ...loop, ISSUES: "2171" })).ok).toBe(false);
    expect(validateRunJobInstruction(instruction({ ...loop, LOOP_MILESTONES: undefined })).ok).toBe(false);
    expect(validateRunJobInstruction(instruction({ ...loop, LOOP_NAME: "-bad" })).ok).toBe(false);
    expect(validateRunJobInstruction(instruction({ ...loop, LOOP_ANSWERS: "12=maybe" })).ok).toBe(false);
  });

  it("ループの入力は new では許さず、ほかの入力は LOOP_NAME を要る（#2347）", () => {
    expect(validateRunJobInstruction(instruction({ LOOP_NAME: "l1" })).ok).toBe(false);
    expect(validateRunJobInstruction(instruction({ START: "resume", ISSUES: undefined, EXCLUDE_ISSUES: "1" })).ok).toBe(false);
    expect(
      validateRunJobInstruction(instruction({ START: "resume", ISSUES: undefined, LOOP_NAME: "l1", EXCLUDE_ISSUES: "1" })).ok,
    ).toBe(true);
  });

  it("START が release は RELEASE_SEQ と RELEASE_STEP を要り、ほかの START は配布の手を許さない（#2439）", () => {
    const release = { START: "release", ISSUES: undefined, RELEASE_SEQ: "3", RELEASE_STEP: "confirm:1" };
    expect(validateRunJobInstruction(instruction(release)).ok).toBe(true);
    expect(validateRunJobInstruction(instruction({ ...release, RELEASE_STEP: undefined })).ok).toBe(false);
    expect(validateRunJobInstruction(instruction({ ...release, RELEASE_STEP: "confirm:3" })).ok).toBe(false);
    expect(validateRunJobInstruction(instruction({ ...release, ISSUES: "1" })).ok).toBe(false);
    expect(validateRunJobInstruction(instruction({ RELEASE_SEQ: "3", RELEASE_STEP: "present" })).ok).toBe(false);
    expect(validateRunJobInstruction(instruction({ START: "resume", ISSUES: undefined, RELEASE_REQUEST_ID: "run-sr1-rabc" })).ok).toBe(true);
  });

  it("知らない入力・番号でない ISSUES は落ちる", () => {
    expect(validateRunJobInstruction({ ...instruction(), EXTRA: "x" }).ok).toBe(false);
    expect(validateRunJobInstruction(instruction({ ISSUES: "2171,abc" })).ok).toBe(false);
  });
});

describe("進捗の事象の定義（受け入れ条件 1・11）", () => {
  it("種類が確定仕様の 13 種類と #2347 の 3 種類と #2439 の 5 種類に 1 対 1 に対応する", () => {
    expect([...EVENT_TYPES].sort()).toEqual([...SPEC_EVENT_TYPES].sort());
    expect(progressEventSchema.oneOf).toHaveLength(SPEC_EVENT_TYPES.length);
  });

  it("共通の列が確定仕様の「進捗の正本」の列と 1 対 1 に対応する", () => {
    const common = progressEventSchema.$defs.EventCommon;
    expect(Object.keys(common.properties).sort()).toEqual([...SPEC_COMMON_COLUMNS].sort());
    expect([...common.required].sort()).toEqual([...SPEC_COMMON_COLUMNS].sort());
  });

  it.each(SPEC_EVENT_TYPES)("%s の正しい事象は通る", (type) => {
    expect(validateProgressEvent(event(type))).toMatchObject({ ok: true });
  });

  it("知らない種類は /type の enum で落ちる", () => {
    expect(validateProgressEvent(event("job.paused", { payload: {} }))).toEqual({
      ok: false,
      violation: { instancePath: "/type", keyword: "enum" },
    });
  });

  it("schema_version 2 は落ちる", () => {
    expect(validateProgressEvent(event("job.started", { schema_version: 2 }))).toMatchObject({
      ok: false,
      violation: { instancePath: "/schema_version" },
    });
  });

  it("payload の余分な鍵・共通の列の欠け・余分な列は落ちる", () => {
    expect(validateProgressEvent(event("job.started", { payload: { ...PAYLOADS["job.started"], extra: 1 } })).ok).toBe(false);
    const missing: Record<string, unknown> = event("job.started");
    delete missing.idempotency_key;
    expect(validateProgressEvent(missing).ok).toBe(false);
    expect(validateProgressEvent(event("job.started", { order_key: "x" })).ok).toBe(false);
  });

  it("UTC でない時刻・# を含む識別子は落ちる", () => {
    expect(validateProgressEvent(event("job.started", { at: "2026-10-30T10:00:00+09:00" })).ok).toBe(false);
    expect(validateProgressEvent(event("job.started", { at: "昨日" })).ok).toBe(false);
    expect(validateProgressEvent(event("job.started", { sprint_run_id: "sr#1" })).ok).toBe(false);
  });

  it("gate.answered の decision は、判断では持たず、ゲートでは必須", () => {
    const answered = (payload: Record<string, unknown>) => validateProgressEvent(event("gate.answered", { payload })).ok;
    const base = { approval_request_id: "ar-1", answered_by_account_id: "acc-1" };
    expect(answered({ ...base, kind: "decision" })).toBe(true);
    expect(answered({ ...base, kind: "decision", decision: "approved" })).toBe(false);
    expect(answered({ ...base, kind: "gate2" })).toBe(false);
    expect(answered({ ...base, kind: "gate2", decision: "rejected" })).toBe(true);
  });

  it("step.recorded の Pull Request の参照は任意で、形が違えば落ちる", () => {
    const step = (pull_request: unknown) =>
      validateProgressEvent(event("step.recorded", { payload: { ...PAYLOADS["step.recorded"], pull_request } })).ok;
    expect(step({ repo: "Owner/Name", number: 3 })).toBe(true);
    expect(step({ repo: "owner", number: 3 })).toBe(false);
    expect(step({ repo: "o/r", number: 0 })).toBe(false);
  });

  it("step.recorded の工程の中身（#2172）はどれも任意で、足す前の形も足した形も通り、上限と形の違いで落ちる", () => {
    const step = (patch: Record<string, unknown>) =>
      validateProgressEvent(event("step.recorded", { payload: { ...PAYLOADS["step.recorded"], ...patch } })).ok;
    const sha = "a".repeat(40);
    const full = {
      phase: "実装",
      issues: [2172],
      files: [{ path: "factory/a.ts", added: 3, deleted: 1 }, { path: "img.png", added: null, deleted: null }],
      files_omitted: 0,
      commit_range: { from: sha, to: "b".repeat(40) },
      decisions: [{ question: "どちらにする", answer: "A", by: "ai" }],
      output: { kind: "report", text: "## 作業の報告", truncated: false },
    };
    expect(step({})).toBe(true);
    expect(step(full)).toBe(true);
    // 列挙で縛らない（NDF の版がフェーズを足しても残る）
    expect(step({ phase: "準備" })).toBe(true);
    expect(step({ issues: [], files: [], decisions: [] })).toBe(true);
    expect(step({ phase: "" })).toBe(false);
    expect(step({ issues: Array.from({ length: 51 }, (_, i) => i + 1) })).toBe(false);
    expect(step({ files: Array.from({ length: 101 }, (_, i) => ({ path: `f${i}`, added: 0, deleted: 0 })) })).toBe(false);
    expect(step({ files: [{ path: "x".repeat(513), added: 0, deleted: 0 }] })).toBe(false);
    expect(step({ commit_range: { from: "abc", to: sha } })).toBe(false);
    expect(step({ decisions: [{ question: "q", answer: "a", by: "someone" }] })).toBe(false);
    expect(step({ decisions: Array.from({ length: 21 }, () => ({ question: "q", answer: "a", by: "ai" })) })).toBe(false);
    expect(step({ output: { kind: "diff", text: "", truncated: false } })).toBe(false);
    expect(step({ output: { kind: "log", text: "x".repeat(32_769), truncated: true } })).toBe(false);
  });

  it("フェーズの名前は 5 つで、isPhaseName は列挙の外を偽にする（#2172）", () => {
    expect(PHASE_NAMES).toEqual(["設計", "実装", "検査", "取り込み", "仕上げ"]);
    expect(PHASE_NAMES.every(isPhaseName)).toBe(true);
    expect(isPhaseName("配布（本番）")).toBe(false);
    expect(isPhaseName(null)).toBe(false);
  });

  it("issue.requested は起票先・題・本文の 3 つで、題は 256 字まで・本文は空にできない（#2216）", () => {
    const requested = (payload: Record<string, unknown>) => validateProgressEvent(event("issue.requested", { payload })).ok;
    const base = PAYLOADS["issue.requested"];
    expect(requested({ ...base, title: "あ".repeat(256) })).toBe(true);
    expect(requested({ ...base, title: "あ".repeat(257) })).toBe(false);
    expect(requested({ ...base, body: "" })).toBe(false);
    expect(requested({ ...base, repo: "org-b" })).toBe(false);
    expect(requested({ ...base, labels: ["bug"] })).toBe(false);
  });

  it("issue.request_resolved は filed なら Issue の参照だけを、drafted なら理由だけを持つ（#2216）", () => {
    const resolved = (payload: Record<string, unknown>) => validateProgressEvent(event("issue.request_resolved", { payload })).ok;
    const filed = PAYLOADS["issue.request_resolved"]!;
    const drafted = { request_key: "job-1:3", outcome: "drafted", reason: "not_registered", duplicate_of: null };
    expect(resolved(drafted)).toBe(true);
    expect(resolved({ ...drafted, reason: "unknown" })).toBe(false);
    expect(resolved({ ...drafted, issue: filed.issue })).toBe(false);
    expect(resolved({ ...filed, reason: "not_registered" })).toBe(false);
    expect(resolved({ ...filed, issue: undefined })).toBe(false);
  });

  it("job.finished の理由は任意で、resets_at は usage_limit のときだけ持てる（#2347）", () => {
    const finished = (payload: Record<string, unknown>) => validateProgressEvent(event("job.finished", { payload })).ok;
    const base = { result: "stopped", summary: "止めた" };
    expect(finished({ ...base, reason: "section_limit" })).toBe(true);
    expect(finished({ ...base, reason: "usage_limit", resets_at: "2026-11-02T05:00:00Z" })).toBe(true);
    expect(finished({ ...base, reason: "usage_limit", resets_at: null })).toBe(true);
    expect(finished({ ...base, reason: "section_limit", resets_at: null })).toBe(false);
    expect(finished({ ...base, reason: "other" })).toBe(false);
  });

  it("loop.stopped の条件は NDF の 10 個だけで、gate.reached の loop は名前と周回を持つ（#2347）", () => {
    const stopped = (condition: string) =>
      validateProgressEvent(event("loop.stopped", { payload: { ...PAYLOADS["loop.stopped"], condition } })).ok;
    for (const c of [
      "user_stop",
      "mvv_changed",
      "same_failure",
      "budget_laps",
      "budget_usd",
      "budget_hours",
      "no_issue",
      "outside_mvv",
      "gate2_returned",
      "refused",
    ]) {
      expect(stopped(c)).toBe(true);
    }
    expect(stopped("slots")).toBe(false);
    const gate = (loop: unknown) =>
      validateProgressEvent(event("gate.reached", { payload: { ...PAYLOADS["gate.reached"], loop } })).ok;
    expect(gate({ name: "lfactory2", lap: 3 })).toBe(true);
    expect(gate({ name: "lfactory2" })).toBe(false);
  });

  it("section.started の指示は 2,000 字まで（#2347）", () => {
    const section = (command: string) =>
      validateProgressEvent(event("section.started", { payload: { section: 1, command } })).ok;
    expect(section("あ".repeat(2000))).toBe(true);
    expect(section("あ".repeat(2001))).toBe(false);
  });

  it("配列や null の本文は最上位の type で落ちる", () => {
    expect(validateProgressEvent([])).toEqual({ ok: false, violation: { instancePath: "", keyword: "type" } });
    expect(validateProgressEvent(null)).toEqual({ ok: false, violation: { instancePath: "", keyword: "type" } });
  });
});

describe("手元のマシンの申告の定義（#2347）", () => {
  it("runner_id は 32 字の英数字で、max_jobs は 1〜16 の整数・任意", () => {
    const id = "a".repeat(32);
    expect(validateLocalRunnerDeclaration({ runner_id: id }).ok).toBe(true);
    expect(validateLocalRunnerDeclaration({ runner_id: id, max_jobs: 16, capacity_hint: 6 }).ok).toBe(true);
    expect(validateLocalRunnerDeclaration({ runner_id: id, max_jobs: 17 }).ok).toBe(false);
    expect(validateLocalRunnerDeclaration({ runner_id: "short" }).ok).toBe(false);
  });
});

describe("受け口の応答の定義", () => {
  it("202 と 4 つのエラーの本文が定義に合い、ほかは合わない", () => {
    expect(isIngestResponse({ accepted: true })).toBe(true);
    for (const code of ["invalid_event", "unauthorized", "forbidden", "ingest_unavailable"]) {
      expect(isIngestResponse({ error: { code, message: "m" } })).toBe(true);
    }
    expect(isIngestResponse({ accepted: false })).toBe(false);
    expect(isIngestResponse({ error: { code: "other", message: "m" } })).toBe(false);
  });
});
