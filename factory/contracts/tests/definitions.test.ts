import { describe, expect, it } from "vitest";
import {
  EVENT_TYPES,
  isIngestResponse,
  progressEventSchema,
  runJobInstructionSchema,
  validateProgressEvent,
  validateRunJobInstruction,
} from "../src/index.ts";

// 確定仕様 docs/specifications/ndf-factory-service-boundary.md の「実行ジョブの指示」の入力（11 項目）
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
];

// 同じ確定仕様の「進捗の事象の種類と受け口」の種類（11 種類）
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
  it("入力の名前が確定仕様の 11 項目と 1 対 1 に対応する", () => {
    expect(Object.keys(runJobInstructionSchema.properties).sort()).toEqual([...SPEC_INSTRUCTION_INPUTS].sort());
  });

  it("new・managed の正しい指示は通る", () => {
    expect(validateRunJobInstruction(instruction()).ok).toBe(true);
  });

  it("START が new で ISSUES が無いと落ちる", () => {
    expect(validateRunJobInstruction(instruction({ ISSUES: undefined })).ok).toBe(false);
  });

  it("START が resume で APPROVAL_REQUEST_ID が無いと落ち、あれば通る", () => {
    expect(validateRunJobInstruction(instruction({ START: "resume", ISSUES: undefined })).ok).toBe(false);
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

  it("知らない入力・番号でない ISSUES は落ちる", () => {
    expect(validateRunJobInstruction({ ...instruction(), EXTRA: "x" }).ok).toBe(false);
    expect(validateRunJobInstruction(instruction({ ISSUES: "2171,abc" })).ok).toBe(false);
  });
});

describe("進捗の事象の定義（受け入れ条件 1・11）", () => {
  it("種類が確定仕様の 11 種類と 1 対 1 に対応する", () => {
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

  it("配列や null の本文は最上位の type で落ちる", () => {
    expect(validateProgressEvent([])).toEqual({ ok: false, violation: { instancePath: "", keyword: "type" } });
    expect(validateProgressEvent(null)).toEqual({ ok: false, violation: { instancePath: "", keyword: "type" } });
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
