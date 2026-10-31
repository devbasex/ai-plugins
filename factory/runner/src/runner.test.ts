import { describe, expect, it, vi } from "vitest";
import { validateProgressEvent, type GateReachedEvent, type ProgressEvent, type RunJobInstruction } from "@ndf-factory/contracts";
import { GITHUB_TOKEN_REFRESH_MS, runJob, type JobRuntime } from "./main.ts";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { claudeEnv, ROLE_PROFILE, type AssumeRole } from "./model.ts";
import { createSender } from "./send.ts";
import { trackedEntries } from "./state.ts";
import {
  common,
  delegatedFromRecord,
  issueRequestFromLine,
  verdictSourcesOf,
  findPullRequest,
  gateRecordName,
  isSprintState,
  rejectionDirective,
  MAX_EVENT_BYTES,
  phaseOf,
  translateProgressLine,
  truncateGate,
  usageFromResult,
} from "./translate.ts";

const ID = { sprintRunId: "sr1", jobId: "job1", repo: "org-a/repo-a" };
const AT = "2026-10-31T00:00:00.000Z";
const full = (seq: number, body: Pick<ProgressEvent, "type" | "payload">) => ({ ...common(ID, seq, AT), ...body }) as ProgressEvent;

describe("翻訳（受け入れ条件 25）", () => {
  it("progress.jsonl の step と attention を契約に合う事象にし、REPO の Pull Request だけを付ける", () => {
    const step = translateProgressLine(
      { kind: "step", step: "pr", exit: 0, seconds: 3.2, summary: JSON.stringify({ summary: "作った https://github.com/Org-A/Repo-A/pull/7 と https://github.com/org-b/x/pull/1" }) },
      ID.repo,
    )!;
    expect(step.payload).toMatchObject({ step: "pr", exit_code: 0, pull_request: { repo: "Org-A/Repo-A", number: 7 } });
    expect(findPullRequest("https://github.com/org-b/x/pull/1", ID.repo)).toBeNull();
    const attention = translateProgressLine({ kind: "attention", reason: "関門", text: "止まった" }, ID.repo)!;
    expect(attention.payload).toEqual({ reason: "関門", summary: "止まった" });
    for (const [i, body] of [step, attention].entries()) expect(validateProgressEvent(full(i + 1, body)).ok).toBe(true);
    expect(translateProgressLine({ kind: "run" }, ID.repo)).toBeNull();
  });

  it("step 行の pr の URL から Pull Request を取る（summary が切れていても）", () => {
    const step = translateProgressLine({ kind: "step", step: "pr", summary: "作った", pr: "https://github.com/org-a/repo-a/pull/9" }, ID.repo)!;
    expect(step.payload).toMatchObject({ pull_request: { repo: "org-a/repo-a", number: 9 } });
  });

  it("答えの記録: 関門の名前を NDF の名前へ写し、差し戻しを明示し、状態ファイルを目録と見分ける", () => {
    expect(gateRecordName("gate1")).toBe("関門 1");
    expect(gateRecordName("gate2")).toBe("関門 2");
    expect(rejectionDirective("関門 2", "テストが足りない")).toMatch(/差し戻し.*テストが足りない/);
    expect(isSprintState({ name: "s", plans: [], gates: [] })).toBe(true);
    expect(isSprintState({ ステージ: [], plans: [], gates: [] })).toBe(false);
    expect(isSprintState({ ステージ: [] })).toBe(false);
  });

  it("計画の名前からフェーズと課題、stream-json の modelUsage からモデルごとの usage を作る", () => {
    expect(phaseOf("5-impl-2171")).toEqual({ phase: "impl", issues: [2171] });
    const usage = usageFromResult({ modelUsage: { opus: { inputTokens: 10, outputTokens: 5, cacheReadInputTokens: 100, cacheCreationInputTokens: 1, costUSD: 0.2 } } }, "conductor");
    expect(usage[0]!.payload).toEqual({ model: "opus", input_tokens: 111, output_tokens: 5, cost_usd: 0.2, step: "conductor" });
    expect(validateProgressEvent(full(1, usage[0]!)).ok).toBe(true);
  });

  it("代わりの判断の記録から decision.delegated を作り、形の違う記録は送らない（AC18）", () => {
    const body = delegatedFromRecord({ question: "q", answer: "a", reason: "r", category: "AskUserQuestion" })!;
    expect(validateProgressEvent(full(1, body)).ok).toBe(true);
    expect(delegatedFromRecord({ question: "q" })).toBeNull();
    // ask_ledger.py の decision の行は category を持たず、ref の判定の行の source から埋める
    const sources = verdictSourcesOf([{ kind: "verdict", id: "v1", source: "hook" }, { kind: "verdict", id: "v2", source: "" }]);
    expect(delegatedFromRecord({ kind: "decision", ref: "v1", question: "q", answer: "a", reason: "r" }, sources)!.payload.category).toBe("hook");
    expect(delegatedFromRecord({ kind: "decision", ref: "v2", question: "q", answer: "a", reason: "r" }, sources)!.payload.category).toBeNull();
  });

  it("起票の依頼の行から issue.requested を作り、上限を超える行は attention、形の違う行は送らない（#2216）", () => {
    const body = issueRequestFromLine({ repo: "org-b/repo-b", title: "直してほしい", body: "由来: org-a/repo-a#1" })!;
    expect(body).toEqual({ type: "issue.requested", payload: { repo: "org-b/repo-b", title: "直してほしい", body: "由来: org-a/repo-a#1" } });
    expect(validateProgressEvent(full(1, body)).ok).toBe(true);
    const tooLarge = issueRequestFromLine({ repo: "org-b/repo-b", title: "あ".repeat(257), body: "b" })!;
    expect(tooLarge).toMatchObject({ type: "attention", payload: { reason: "issue_request_too_large" } });
    expect(validateProgressEvent(full(1, tooLarge)).ok).toBe(true);
    expect(issueRequestFromLine({ repo: "org-b/repo-b", title: "t", body: "あ".repeat(50_001) })).toMatchObject({ type: "attention" });
    expect(issueRequestFromLine({ repo: "org-b", title: "t", body: "b" })).toBeNull();
    expect(issueRequestFromLine({ repo: "org-b/repo-b", title: "", body: "b" })).toBeNull();
  });

  it("300 KiB の承認資料を 256 KiB 以下に切り、案内を残す（AC26）", () => {
    const event = full(1, { type: "gate.reached", payload: { kind: "gate1", pr_url: "https://github.com/org-a/repo-a/pull/7", material: "あ".repeat(100_000) } }) as GateReachedEvent;
    const cut = truncateGate(event);
    expect(Buffer.byteLength(JSON.stringify(cut), "utf8")).toBeLessThanOrEqual(MAX_EVENT_BYTES);
    expect(cut.payload.material).toContain("全文は https://github.com/org-a/repo-a/pull/7 にある");
    expect(validateProgressEvent(cut).ok).toBe(true);
  });
});

describe("送信（受け入れ条件 25）", () => {
  it("5xx と接続の失敗は同じ冪等の鍵で送り直し、4xx は送り直さない", async () => {
    const bodies: string[] = [];
    const statuses: (number | Error)[] = [503, new Error("ECONNRESET"), 202];
    const send = createSender({
      url: "https://api.example/ingest/events",
      token: () => "t",
      fetch: (async (_u: string, init: RequestInit) => {
        bodies.push(String(init.body));
        const s = statuses.shift()!;
        if (s instanceof Error) throw s;
        return new Response(null, { status: s });
      }) as typeof fetch,
      sleep: async () => undefined,
      stderr: () => undefined,
    });
    const event = full(1, { type: "attention", payload: { reason: "r", summary: "s" } });
    expect(await send(event)).toBe("sent");
    expect(new Set(bodies.map((b) => JSON.parse(b).idempotency_key))).toEqual(new Set(["job1:1"]));
    expect(bodies).toHaveLength(3);
    statuses.push(403);
    expect(await send(event)).toBe("rejected");
    expect(bodies).toHaveLength(4);
  });
});

describe("状態（受け入れ条件 28・32、I42）", () => {
  it("追跡する .ndf/pace.json と状態ディレクトリの外を含む保存を断る", () => {
    const tracked = new Set([".ndf/pace.json", "README.md"]);
    expect(trackedEntries([".ndf/tmp/", ".ndf/tmp/sprint-1/sprint.json"], tracked)).toEqual([]);
    expect(trackedEntries([".ndf/tmp/x", ".ndf/pace.json"], tracked)).toEqual([".ndf/pace.json"]);
    expect(trackedEntries(["src/a.ts"], tracked)).toEqual(["src/a.ts"]);
  });
});

describe("claude の呼び方（受け入れ条件 8・29）", () => {
  const dir = mkdtempSync(join(tmpdir(), "ndf-byok-"));
  const base = { PATH: "/usr/bin", INGEST_TOKEN: "raw", INGEST_TOKEN_SECRET_ARN: "arn:x", MODEL_CREDENTIAL_SECRET_ARN: "arn:y", BEDROCK_REGION: "us-west-2" };
  const assume: AssumeRole = async (cred) => {
    if (cred.external_id !== "ndf-factory:acc-1") throw new Error("AccessDenied");
    return { accessKeyId: "AKIA", secretAccessKey: "s", sessionToken: "t" };
  };

  it("managed は Bedrock とタスクの役割で、API キーもアクセスキーも置かない", async () => {
    const env = await claudeEnv(base, "managed", null, assume, "s", dir);
    expect(env).toMatchObject({ CLAUDE_CODE_USE_BEDROCK: "1", AWS_REGION: "us-west-2" });
    expect(env.ANTHROPIC_API_KEY).toBeUndefined();
    expect(env.AWS_ACCESS_KEY_ID).toBeUndefined();
    expect(env.INGEST_TOKEN_SECRET_ARN).toBeUndefined();
    expect(env.INGEST_TOKEN).toBeUndefined();
  });

  it("local は利用者が手元で設定したモデルの資格を渡し、受け口の token は渡さない", async () => {
    const env = await claudeEnv({ ...base, ANTHROPIC_API_KEY: "mine", AWS_ACCESS_KEY_ID: "AK" }, "local", null, assume, "s", dir);
    expect(env).toMatchObject({ ANTHROPIC_API_KEY: "mine", AWS_ACCESS_KEY_ID: "AK" });
    expect(env.INGEST_TOKEN).toBeUndefined();
    expect(env.MODEL_CREDENTIAL_SECRET_ARN).toBeUndefined();
  });

  it("byok の API キーは子の環境にだけ置き、ロールは外部 ID つきで引き受ける。外部 ID が違えば失敗する", async () => {
    expect((await claudeEnv(base, "byok", { kind: "anthropic_api_key", api_key: "k" }, assume, "s", dir)).ANTHROPIC_API_KEY).toBe("k");
    const role = { kind: "aws_role" as const, role_arn: "arn:aws:iam::123456789012:role/r", region: "us-east-1", external_id: "ndf-factory:acc-1" };
    const env = await claudeEnv(base, "byok", role, assume, "s", dir);
    // 資格は環境変数へ固定せず、親が置き直すファイルを credential_process で読ませる
    expect(env.AWS_SESSION_TOKEN).toBeUndefined();
    expect(env.AWS_PROFILE).toBe(ROLE_PROFILE);
    expect(readFileSync(env.AWS_CONFIG_FILE!, "utf8")).toContain("credential_process");
    expect(JSON.parse(readFileSync(join(dir, "credentials.json"), "utf8"))).toMatchObject({ Version: 1, SessionToken: "t" });
    await expect(claudeEnv(base, "byok", { ...role, external_id: "other" }, assume, "s", dir)).rejects.toThrow();
  });
});

describe("ジョブの流れと終了コード（受け入れ条件 24・27・30・31、I43）", () => {
  const instruction: RunJobInstruction = {
    SPRINT_RUN_ID: "sr1",
    JOB_ID: "job1",
    REPO: "org-a/repo-a",
    NDF_VERSION: "10.26.1",
    START: "resume",
    APPROVAL_REQUEST_ID: "ar-1",
    INGEST_URL: "https://api.example/ingest/events",
    INGEST_TOKEN_SECRET_ARN: "arn:aws:secretsmanager:us-east-1:1:secret:t",
    EXECUTION_MODE: "managed",
  };

  function harness(opts: { installed?: string; ingestStatus?: number } = {}) {
    const events: ProgressEvent[] = [];
    const calls: string[] = [];
    const fetchImpl = (async (url: string, init: RequestInit) => {
      if (url.endsWith("/ingest/events")) {
        events.push(JSON.parse(String(init.body)) as ProgressEvent);
        return new Response(null, { status: opts.ingestStatus ?? 202 });
      }
      calls.push(new URL(url).pathname + (init.body ? String(init.body) : ""));
      return Response.json(url.endsWith("github-token") ? { token: `ghs_${calls.length}` } : { url: "https://state.example/x" });
    }) as typeof fetch;
    const tokens: string[] = [];
    const runtime: JobRuntime = {
      writeGitHubToken: async (t) => void tokens.push(t),
      clone: async () => undefined,
      installNdf: async () => opts.installed ?? "10.26.1",
      restoreState: async () => undefined,
      saveState: async () => undefined,
      recordAnswer: vi.fn(async () => null),
      runConductor: async (_i, _d, emit) => {
        await emit({ type: "phase.changed", payload: { phase: "impl", issues: [1] } });
        return { report: "判断が要る", summary: "判断が要る", gate: { kind: "gate2", pr_url: null, material: "資料" } };
      },
    };
    let tick: (() => void) | null = null;
    return { events, calls, tokens, runtime, fetchImpl, setInterval: (fn: () => void) => ((tick = fn), 1), tickTimer: () => tick?.() };
  }

  it("resume は状態を戻し答えを記録してから続け、gate.reached と job.finished（gate）を送って 0 で終わる", async () => {
    const t = harness();
    const code = await runJob({ instruction, token: "tok", runtime: t.runtime, fetch: t.fetchImpl, setInterval: t.setInterval, clearInterval: () => undefined, stderr: () => undefined });
    expect(code).toBe(0);
    expect(t.runtime.recordAnswer).toHaveBeenCalledWith("ar-1");
    expect(t.events.map((e) => e.type)).toEqual(["job.started", "phase.changed", "gate.reached", "job.finished"]);
    expect(t.events.at(-1)).toMatchObject({ idempotency_key: "job1:finished", payload: { result: "gate" } });
    expect(t.calls.some((c) => c.includes('"op":"get"'))).toBe(true);
    expect(t.calls.some((c) => c.includes('"op":"put"'))).toBe(true);
  });

  it("50 分ごとに導入の token を取り直す（AC30）", async () => {
    const t = harness();
    t.runtime.runConductor = async () => {
      t.tickTimer();
      await new Promise((r) => setTimeout(r, 0));
      return { report: "完了", summary: "完了", gate: null };
    };
    await runJob({ instruction, token: "tok", runtime: t.runtime, fetch: t.fetchImpl, setInterval: t.setInterval, clearInterval: () => undefined, stderr: () => undefined });
    expect(t.tokens.length).toBe(2);
    expect(GITHUB_TOKEN_REFRESH_MS).toBe(50 * 60 * 1000);
  });

  it("入った版が違えば failed、job.finished が送れなければ 1（AC24・AC31）", async () => {
    const t = harness({ installed: "10.27.0" });
    expect(await runJob({ instruction, token: "tok", runtime: t.runtime, fetch: t.fetchImpl, setInterval: t.setInterval, clearInterval: () => undefined, stderr: () => undefined })).toBe(0);
    expect(t.events.at(-1)!.payload).toMatchObject({ result: "failed" });
    const u = harness({ ingestStatus: 400 });
    expect(await runJob({ instruction, token: "tok", runtime: u.runtime, fetch: u.fetchImpl, setInterval: u.setInterval, clearInterval: () => undefined, stderr: () => undefined })).toBe(1);
  });
});
