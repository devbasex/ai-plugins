import { beforeEach, describe, expect, it } from "vitest";
import type { ProgressEvent } from "@ndf-factory/contracts";
import { AccountService } from "../accounts/service.ts";
import { MemoryAccountStore } from "../accounts/memory-store.ts";
import { CredentialService, type SecretsPort } from "../credentials/service.ts";
import { createApiApp } from "../app.ts";
import type { GitHubPort } from "../github/port.ts";
import { appendEvent } from "../progress/append.ts";
import { memoryProgressReader } from "../progress/read.ts";
import { RepositoryService } from "../repositories/service.ts";
import { LoopControl } from "../runs/loops.ts";
import type { SprintRun } from "../runs/model.ts";
import { createRunsHarness, type RunsHarness } from "../testing/runs.ts";
import { createTokenKit, type TokenKit } from "../testing/tokens.ts";
import { firstResult, type TestResponse } from "../testing/app.ts";
import type { FactoryDeps } from "./factory.ts";

const noSecrets: SecretsPort = { create: async () => "arn", put: async () => undefined, delete: async () => undefined };

let h: RunsHarness;
let tokens: TokenKit;
let app: ReturnType<typeof createApiApp>;
let githubCalls: string[];
let factory: FactoryDeps;
let accounts: AccountService;

async function call(path: string, user = "u-1"): Promise<TestResponse> {
  return (await app.request(path, { headers: { Authorization: await tokens.bearer(user) } })) as TestResponse;
}

// 要素 1 個の結果を状態と本文で読む（#2384）
const read = async (path: string, user = "u-1") => firstResult(await call(path, user));
const detailPath = (acc: string, id: string) => `/accounts/${acc}/sprint-runs/detail?sprint_run_id=${encodeURIComponent(id)}`;
const stepPath = (acc: string, id: string, key: string) => `/accounts/${acc}/sprint-runs/steps?sprint_run_id=${encodeURIComponent(id)}&step_key=${encodeURIComponent(key)}`;
const backlogPath = (acc: string, repositoryId: number) => `/accounts/${acc}/backlogs?repository_id=${repositoryId}`;
const approvalsPath = (acc: string) => `/accounts/${acc}/sprint-runs/approval-requests`;

async function personal(user: string): Promise<string> {
  return ((await (await call("/me", user)).json())[0] as { personal_account_id: string }).personal_account_id;
}

function runOf(accountId: string, patch: Partial<SprintRun> = {}): SprintRun {
  return {
    sprintRunId: "sr-1",
    accountId,
    repositoryId: 101,
    repo: "org-a/repo-a",
    state: "running",
    issues: [11, 12],
    ndfVersion: "10.27.15",
    executionMode: "byok",
    modelCredentialSecretArn: null,
    currentJobId: "job-1",
    jobCount: 1,
    cancelRequested: false,
    failureReason: null,
    createdBy: "u-1",
    createdAt: "2026-11-04T00:00:00.000Z",
    updatedAt: "2026-11-04T00:00:00.000Z",
    ...patch,
  };
}

let seq = 0;
async function emit(sprintRunId: string, type: string, payload: Record<string, unknown>, at?: string) {
  seq += 1;
  const event = {
    sprint_run_id: sprintRunId,
    idempotency_key: `job-1:${seq}`,
    type,
    at: at ?? new Date(Date.UTC(2026, 10, 4, 0, 0, seq)).toISOString(),
    source: "runner",
    job_id: "job-1",
    schema_version: 1,
    payload,
  } as unknown as ProgressEvent;
  await appendEvent(h.progress, event);
  return event.idempotency_key;
}

beforeEach(async () => {
  h = await createRunsHarness();
  tokens = await createTokenKit();
  seq = 0;
  githubCalls = [];
  // 偽の GitHub の口の呼び出しを数える（I13）
  const github = new Proxy(h.github, {
    get(target, prop, receiver) {
      const value = Reflect.get(target, prop, receiver);
      return typeof value === "function"
        ? (...args: unknown[]) => {
            githubCalls.push(String(prop));
            return (value as (...a: unknown[]) => unknown).apply(target, args);
          }
        : value;
    },
  }) as GitHubPort;
  const store = new MemoryAccountStore();
  let n = 0;
  accounts = new AccountService({ store, newId: () => `00000000-0000-4000-8000-${String(++n).padStart(12, "0")}` });
  const reader = memoryProgressReader(h.progress);
  factory = {
    repositories: new RepositoryService({ table: h.table, github, proofKey: async () => new Uint8Array(32) }),
    credentials: new CredentialService(h.table, noSecrets),
    control: h.control,
    loops: new LoopControl({ table: h.table, control: h.control, reader, now: () => h.clock.now }),
    organizer: h.organizer,
    table: h.table,
    github,
    candidateModel: async () => [],
    reader,
  };
  app = createApiApp(() => ({ verifier: tokens.verifier, accounts }), () => undefined, () => null, () => factory);
});

async function seed() {
  const acc = await personal("u-1");
  await h.table.putRegistration({ accountId: acc, repositoryId: 101, fullName: "org-a/repo-a", installationId: 9, registeredBy: "u-1", registeredAt: "2026-11-01T00:00:00Z" });
  h.table.runs.set("sr-1", runOf(acc));
  h.table.runs.set("sr-2", runOf(acc, { sprintRunId: "sr-2", issues: [20], state: "awaiting_approval", createdAt: "2026-11-04T01:00:00.000Z" }));
  await emit("sr-1", "phase.changed", { phase: "実装", issues: [11] });
  const step = await emit("sr-1", "step.recorded", {
    step: "impl",
    exit_code: 0,
    seconds: 5,
    summary: "済んだ",
    issues: [11],
    output: { kind: "report", text: "## 作業の報告", truncated: false },
  });
  await emit("sr-2", "phase.changed", { phase: "準備", issues: [] });
  return { acc, step };
}

describe("一覧の経路（受け入れ条件 1〜3・18）", () => {
  it("既存の 6 項目の名前と型を変えずに、repo・今のフェーズ・今の工程・最後の動きを足す。5 つに無い名前も 200", async () => {
    const { acc } = await seed();
    const res = await call(`/accounts/${acc}/sprint-runs`);
    expect(res.status).toBe(200);
    const sprint_runs = await res.json();
    expect(sprint_runs.map((r: { sprint_run_id: string }) => r.sprint_run_id)).toEqual(["sr-2", "sr-1"]);
    const row = sprint_runs[1];
    expect(row).toEqual({
      sprint_run_id: "sr-1",
      repository_id: 101,
      issues: [11, 12],
      state: "running",
      failure_reason: null,
      created_at: "2026-11-04T00:00:00.000Z",
      repo: "org-a/repo-a",
      current_phase: "実装",
      current_step: "impl",
      last_event_at: "2026-11-04T00:00:02.000Z",
    });
    expect(sprint_runs[0].current_phase).toBe("準備");
  });
});

describe("詳細と工程の中身の経路（受け入れ条件 7・13・14）", () => {
  it("詳細は本文を返さず、工程の中身は 1 件ずつ本文を返す。どちらも GitHub の口を呼ばない", async () => {
    const { acc, step } = await seed();
    const detail = await read(detailPath(acc, "sr-1"));
    expect(detail.status).toBe(200);
    const body = detail.body;
    expect(body).toMatchObject({ sprint_run_id: "sr-1", repo: "org-a/repo-a", current_phase: "実装", ndf_version: "10.27.15", job_count: 1, omitted_steps: 0 });
    expect(body.steps[0]).toMatchObject({ key: step, phase: "実装", issues: [11], has_details: true });
    expect(JSON.stringify(body)).not.toContain("## 作業の報告");
    const one = await read(stepPath(acc, "sr-1", step));
    expect(one.status).toBe(200);
    expect(one.body.output).toEqual({ kind: "report", text: "## 作業の報告", truncated: false });
    await call(`/accounts/${acc}/sprint-runs`);
    expect(githubCalls).toEqual([]);
  });

  it("足す前の形の step.recorded（schema_version 1 のまま）も詳細に出て、本文の欄は null", async () => {
    const { acc } = await seed();
    const key = await emit("sr-1", "step.recorded", { step: "pr", exit_code: 0, seconds: 1, summary: "作った" });
    const body = (await read(detailPath(acc, "sr-1"))).body;
    expect(body.steps.map((s: { key: string }) => s.key)).toContain(key);
    const one = (await read(stepPath(acc, "sr-1", key))).body;
    expect([one.files, one.decisions, one.output]).toEqual([null, null, null]);
  });

  it("読み出しは事象の表と実行の表を書き換えない（I14）", async () => {
    const { acc, step } = await seed();
    const before = JSON.stringify([h.progress.events, [...h.table.runs.values()]]);
    await call(`/accounts/${acc}/sprint-runs`);
    await call(detailPath(acc, "sr-1"));
    await call(stepPath(acc, "sr-1", step));
    await call(backlogPath(acc, 101));
    expect(JSON.stringify([h.progress.events, [...h.table.runs.values()]])).toBe(before);
  });
});

describe("認可（受け入れ条件 11・I12）", () => {
  it("他人のアカウント・無いアカウント・属さない実行・無い実行・無い工程・登録の無いリポジトリは、同じ本文の 403", async () => {
    const { acc, step } = await seed();
    const other = await personal("u-2");
    h.table.runs.set("sr-other", runOf(other, { sprintRunId: "sr-other" }));
    const paths = [`/accounts/${acc}/sprint-runs`, detailPath(acc, "sr-1"), stepPath(acc, "sr-1", step), backlogPath(acc, 101)];
    const missing = "00000000-0000-4000-8000-0000000000ff";
    const cases = [
      ...paths.map((p) => [p, "u-2"] as const),
      ...paths.map((p) => [p.replace(acc, missing), "u-1"] as const),
      [detailPath(acc, "sr-other"), "u-1"],
      [detailPath(acc, "sr-none"), "u-1"],
      [stepPath(acc, "sr-other", step), "u-1"],
      [stepPath(acc, "sr-1", "job-1:999"), "u-1"],
      [stepPath(acc, "sr-2", step), "u-1"],
      [backlogPath(acc, 999), "u-1"],
    ] as const;
    // 要求全体の 403 と要素の forbidden は、同じ本文を持つ
    const bodies = new Set<string>();
    for (const [path, user] of cases) {
      const res = await read(path, user);
      expect(res.status, path).toBe(403);
      bodies.add(JSON.stringify(res.body));
    }
    expect([...bodies]).toEqual([JSON.stringify({ error: { code: "forbidden", message: "このアカウントを見る権限がありません" } })]);
  });
});

describe("バックログの経路（受け入れ条件 15・16・I13）", () => {
  it("順位の表と、そのリポジトリの棚卸しの記録を新しい順に返し、GitHub は順位の表の読み出しだけを呼ぶ", async () => {
    const { acc } = await seed();
    h.github.rankings.set("org-a/repo-a", { milestone: "25 見本", rows: [{ number: 5, title: "先に" }, { number: 3, title: "後に" }] });
    await emit("sr-1", "step.recorded", { step: "refine", exit_code: 0, seconds: 3, summary: "古い棚卸し" }, "2026-11-02T00:00:00.000Z");
    await emit("sr-2", "step.recorded", { step: "refine", exit_code: 1, seconds: 3, summary: "新しい棚卸し" }, "2026-11-03T00:00:00.000Z");
    const res = await read(backlogPath(acc, 101));
    expect(res.status).toBe(200);
    const body = res.body;
    expect(body.repo).toBe("org-a/repo-a");
    expect(body.ranking).toEqual({
      milestone: "25 見本",
      rows: [
        { rank: 1, number: 5, title: "先に" },
        { rank: 2, number: 3, title: "後に" },
      ],
    });
    expect(body.refinements.map((r: { summary: string }) => r.summary)).toEqual(["新しい棚卸し", "古い棚卸し"]);
    expect(githubCalls).toEqual(["findRankingTable"]);
  });

  it("順位の表が無ければ ranking は null、GitHub の口が失敗すれば 503", async () => {
    const { acc } = await seed();
    const none = (await read(backlogPath(acc, 101))).body;
    expect(none.ranking).toBeNull();
    expect(none.refinements).toEqual([]);
    h.github.findRankingTable = async () => {
      throw new Error("GitHub 502");
    };
    expect((await read(backlogPath(acc, 101))).status).toBe(503);
  });
});

describe("配列の受け渡し（#2384 の受け入れ条件 6〜8・15、I12・I13）", () => {
  it("3 個の要素のうち他人の 1 個だけが forbidden で、順と数は入力と同じ。他人のデータは応答に無い", async () => {
    const { acc } = await seed();
    const other = await personal("u-2");
    h.table.runs.set("sr-other", runOf(other, { sprintRunId: "sr-other", repo: "org-x/secret-repo" }));
    const res = await call(`/accounts/${acc}/sprint-runs/detail?sprint_run_id=sr-1&sprint_run_id=sr-other&sprint_run_id=sr-2`);
    expect(res.status).toBe(200);
    const results = await res.json();
    expect(results.map((r: { ok: boolean; value?: { sprint_run_id: string }; error?: { code: string } }) => (r.ok ? r.value!.sprint_run_id : r.error!.code))).toEqual([
      "sr-1",
      "forbidden",
      "sr-2",
    ]);
    expect(JSON.stringify(results)).not.toContain("secret-repo");
  });

  it("101 個の要素は、どれも処理せずに 400 と上限 100、0 個は 400", async () => {
    const { acc } = await seed();
    const reads: string[] = [];
    const getRun = h.table.getRun.bind(h.table);
    h.table.getRun = async (id: string) => {
      reads.push(id);
      return getRun(id);
    };
    const many = Array.from({ length: 101 }, (_, i) => `sprint_run_id=sr-${i}`).join("&");
    const res = await call(`/accounts/${acc}/sprint-runs/detail?${many}`);
    expect(res.status).toBe(400);
    expect((await res.json()).error).toMatchObject({ code: "too_many_items", details: { limit: 100 } });
    const empty = await call(`/accounts/${acc}/sprint-runs/detail`);
    expect(empty.status).toBe(400);
    expect((await empty.json()).error.code).toBe("empty_items");
    expect(reads).toEqual([]);
  });

  it("同じ鍵の要素が 2 つあれば 400 duplicate_key", async () => {
    const { acc } = await seed();
    const res = await call(`/accounts/${acc}/sprint-runs/detail?sprint_run_id=sr-1&sprint_run_id=sr-1`);
    expect(res.status).toBe(400);
    expect((await res.json()).error).toMatchObject({ code: "duplicate_key", details: { key: "sprint_run_id" } });
  });
});

describe("詳細の注意の欄（#2430 の受け入れ条件 1〜4）", () => {
  it("詳細の応答に attention が記録された順に入り、base_conflict 以外の理由も値のまま 200 で返す。0 件は空の配列", async () => {
    const { acc } = await seed();
    const conflict = await emit("sr-1", "attention", { reason: "base_conflict", summary: "src/a.ts で衝突" });
    await emit("sr-1", "attention", { reason: "stopped", summary: "止まった" });
    const res = await read(detailPath(acc, "sr-1"));
    expect(res.status).toBe(200);
    expect(res.body.attention).toEqual([
      { key: conflict, at: "2026-11-04T00:00:04.000Z", reason: "base_conflict", summary: "src/a.ts で衝突" },
      { key: "job-1:5", at: "2026-11-04T00:00:05.000Z", reason: "stopped", summary: "止まった" },
    ]);
    expect((await read(detailPath(acc, "sr-2"))).body.attention).toEqual([]);
  });
});

describe("承認待ちの一覧の経路（#2430 の受け入れ条件 5・6・8・10・I3〜I7）", () => {
  const gate = (kind: string, loop?: { name: string; lap: number }) => ({ kind, pr_url: null, material: "資料", ...(loop ? { loop } : {}) });

  async function seedWaiting(acc: string) {
    const waiting = (id: string, patch: Partial<SprintRun> = {}) =>
      h.table.runs.set(id, runOf(acc, { sprintRunId: id, state: "awaiting_approval", currentJobId: null, ...patch }));
    waiting("sr-a", { issues: [1] });
    waiting("sr-b", { issues: [2], repo: "org-a/repo-b", repositoryId: 102 });
    waiting("sr-c", { issues: [3] });
    waiting("sr-d", { issues: [4] });
    waiting("sr-rel", { issues: [5], awaitingReason: "release_requested" });
    h.table.runs.set("sr-run", runOf(acc, { sprintRunId: "sr-run", issues: [6] }));
    await emit("sr-b", "gate.reached", gate("gate2", { name: "lfactory3", lap: 1 }), "2026-11-04T00:00:02.000Z");
    // (a) は材料を直して出し直した。行は 1 つで、日時は後のもの
    await emit("sr-a", "gate.reached", gate("gate1", { name: "lfactory2", lap: 3 }), "2026-11-04T00:00:01.000Z");
    await emit("sr-a", "gate.reached", gate("gate1", { name: "lfactory2", lap: 3 }), "2026-11-04T00:00:03.000Z");
    await emit("sr-c", "gate.reached", gate("decision"), "2026-11-04T00:00:04.000Z");
    await emit("sr-d", "gate.reached", gate("gate1"), "2026-11-04T00:00:00.500Z");
    await emit("sr-d", "gate.answered", { approval_request_id: "ar-d", kind: "gate1", decision: "approved", answered_by_account_id: acc });
    await emit("sr-rel", "gate.reached", gate("gate2"), "2026-11-04T00:00:00.100Z");
    await emit("sr-run", "gate.reached", gate("gate1"), "2026-11-04T00:00:00.200Z");
  }

  it("答えの無い承認の依頼だけが、種類・ループ・周回・リポジトリ・課題・待ち始めた日時とともに古い順に並ぶ", async () => {
    const acc = await personal("u-1");
    await seedWaiting(acc);
    const res = await call(approvalsPath(acc));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual([
      { sprint_run_id: "sr-b", repository_id: 102, repo: "org-a/repo-b", issues: [2], kind: "gate2", loop: { name: "lfactory3", lap: 1 }, waiting_since: "2026-11-04T00:00:02.000Z" },
      { sprint_run_id: "sr-a", repository_id: 101, repo: "org-a/repo-a", issues: [1], kind: "gate1", loop: { name: "lfactory2", lap: 3 }, waiting_since: "2026-11-04T00:00:03.000Z" },
      { sprint_run_id: "sr-c", repository_id: 101, repo: "org-a/repo-a", issues: [3], kind: "decision", loop: null, waiting_since: "2026-11-04T00:00:04.000Z" },
    ]);
  });

  it("承認の依頼が 0 件なら 200 と空の配列。読み出しは事象の表と実行の表を書き換えない", async () => {
    const { acc } = await seed();
    const before = JSON.stringify([h.progress.events, [...h.table.runs.values()]]);
    const res = await call(approvalsPath(acc));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual([]);
    expect(JSON.stringify([h.progress.events, [...h.table.runs.values()]])).toBe(before);
  });

  it("他人のアカウントの一覧は 403 で、スプリントの一覧と同じ本文。他人の実行は自分の一覧に出ない", async () => {
    const acc = await personal("u-1");
    const other = await personal("u-2");
    h.table.runs.set("sr-other", runOf(other, { sprintRunId: "sr-other", state: "awaiting_approval" }));
    await emit("sr-other", "gate.reached", gate("gate1"));
    const denied = await call(approvalsPath(other), "u-1");
    const list = await call(`/accounts/${other}/sprint-runs`, "u-1");
    expect(denied.status).toBe(403);
    expect(await denied.json()).toEqual(await list.json());
    expect(await (await call(approvalsPath(acc))).json()).toEqual([]);
  });

  it("チームのアカウントは席を持つ人が読める", async () => {
    await personal("u-1");
    await personal("u-2");
    const team = await accounts.createTeam({ userId: "u-1", idp: "google" }, "t", "standard");
    const ctx = await accounts.resolveAccount({ userId: "u-1", idp: "google" }, team.accountId);
    await accounts.addSeat(ctx, "u-2", "standard");
    h.table.runs.set("sr-team", runOf(team.accountId, { sprintRunId: "sr-team", state: "awaiting_approval" }));
    await emit("sr-team", "gate.reached", gate("gate1"));
    const res = await call(approvalsPath(team.accountId), "u-2");
    expect(res.status).toBe(200);
    expect((await res.json()).map((r: { sprint_run_id: string }) => r.sprint_run_id)).toEqual(["sr-team"]);
  });

  it("実行の表か事象の表を読めなければ 503 progress_unavailable", async () => {
    const acc = await personal("u-1");
    await seedWaiting(acc);
    factory.reader = { ...factory.reader, listGateEvents: async () => Promise.reject(new Error("throttled")) };
    const res = await call(approvalsPath(acc));
    expect(res.status).toBe(503);
    expect((await res.json()).error.code).toBe("progress_unavailable");
  });
});
