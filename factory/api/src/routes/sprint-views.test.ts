import { beforeEach, describe, expect, it } from "vitest";
import type { ProgressEvent } from "@ndf-factory/contracts";
import { AccountService } from "../accounts/service.ts";
import { MemoryAccountStore } from "../accounts/memory-store.ts";
import { CredentialService, type SecretsPort } from "../credentials/service.ts";
import { createApiApp } from "../handlers/api.ts";
import type { GitHubPort } from "../github/port.ts";
import { appendEvent } from "../progress/append.ts";
import { memoryProgressReader } from "../progress/read.ts";
import { RepositoryService } from "../repositories/service.ts";
import { LoopControl } from "../runs/loops.ts";
import type { SprintRun } from "../runs/model.ts";
import { createRunsHarness, type RunsHarness } from "../testing/runs.ts";
import { createTokenKit, type TokenKit } from "../testing/tokens.ts";
import type { TestResponse } from "../testing/app.ts";
import type { FactoryDeps } from "./factory.ts";

const noSecrets: SecretsPort = { create: async () => "arn", put: async () => undefined, delete: async () => undefined };

let h: RunsHarness;
let tokens: TokenKit;
let app: ReturnType<typeof createApiApp>;
let githubCalls: string[];
let factory: FactoryDeps;

async function call(path: string, user = "u-1"): Promise<TestResponse> {
  return (await app.request(path, { headers: { Authorization: await tokens.bearer(user) } })) as TestResponse;
}

async function personal(user: string): Promise<string> {
  return ((await (await call("/me", user)).json()) as { personal_account_id: string }).personal_account_id;
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
  const accounts = new AccountService({ store, newId: () => `00000000-0000-4000-8000-${String(++n).padStart(12, "0")}` });
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
    const { sprint_runs } = await res.json();
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
    const detail = await call(`/accounts/${acc}/sprint-runs/sr-1`);
    expect(detail.status).toBe(200);
    const body = await detail.json();
    expect(body).toMatchObject({ sprint_run_id: "sr-1", repo: "org-a/repo-a", current_phase: "実装", ndf_version: "10.27.15", job_count: 1, omitted_steps: 0 });
    expect(body.steps[0]).toMatchObject({ key: step, phase: "実装", issues: [11], has_details: true });
    expect(JSON.stringify(body)).not.toContain("## 作業の報告");
    const one = await call(`/accounts/${acc}/sprint-runs/sr-1/steps/${encodeURIComponent(step)}`);
    expect(one.status).toBe(200);
    expect((await one.json()).output).toEqual({ kind: "report", text: "## 作業の報告", truncated: false });
    await call(`/accounts/${acc}/sprint-runs`);
    expect(githubCalls).toEqual([]);
  });

  it("足す前の形の step.recorded（schema_version 1 のまま）も詳細に出て、本文の欄は null", async () => {
    const { acc } = await seed();
    const key = await emit("sr-1", "step.recorded", { step: "pr", exit_code: 0, seconds: 1, summary: "作った" });
    const body = await (await call(`/accounts/${acc}/sprint-runs/sr-1`)).json();
    expect(body.steps.map((s: { key: string }) => s.key)).toContain(key);
    const one = await (await call(`/accounts/${acc}/sprint-runs/sr-1/steps/${encodeURIComponent(key)}`)).json();
    expect([one.files, one.decisions, one.output]).toEqual([null, null, null]);
  });

  it("読み出しは事象の表と実行の表を書き換えない（I14）", async () => {
    const { acc, step } = await seed();
    const before = JSON.stringify([h.progress.events, [...h.table.runs.values()]]);
    await call(`/accounts/${acc}/sprint-runs`);
    await call(`/accounts/${acc}/sprint-runs/sr-1`);
    await call(`/accounts/${acc}/sprint-runs/sr-1/steps/${encodeURIComponent(step)}`);
    await call(`/accounts/${acc}/repositories/101/backlog`);
    expect(JSON.stringify([h.progress.events, [...h.table.runs.values()]])).toBe(before);
  });
});

describe("認可（受け入れ条件 11・I12）", () => {
  it("他人のアカウント・無いアカウント・属さない実行・無い実行・無い工程・登録の無いリポジトリは、同じ本文の 403", async () => {
    const { acc, step } = await seed();
    const other = await personal("u-2");
    h.table.runs.set("sr-other", runOf(other, { sprintRunId: "sr-other" }));
    const paths = [
      `/accounts/${acc}/sprint-runs`,
      `/accounts/${acc}/sprint-runs/sr-1`,
      `/accounts/${acc}/sprint-runs/sr-1/steps/${encodeURIComponent(step)}`,
      `/accounts/${acc}/repositories/101/backlog`,
    ];
    const missing = "00000000-0000-4000-8000-0000000000ff";
    const cases = [
      ...paths.map((p) => [p, "u-2"] as const),
      ...paths.map((p) => [p.replace(acc, missing), "u-1"] as const),
      [`/accounts/${acc}/sprint-runs/sr-other`, "u-1"],
      [`/accounts/${acc}/sprint-runs/sr-none`, "u-1"],
      [`/accounts/${acc}/sprint-runs/sr-other/steps/${encodeURIComponent(step)}`, "u-1"],
      [`/accounts/${acc}/sprint-runs/sr-1/steps/job-1%3A999`, "u-1"],
      [`/accounts/${acc}/sprint-runs/sr-2/steps/${encodeURIComponent(step)}`, "u-1"],
      [`/accounts/${acc}/repositories/999/backlog`, "u-1"],
    ] as const;
    const bodies = new Set<string>();
    for (const [path, user] of cases) {
      const res = await call(path, user);
      expect(res.status, path).toBe(403);
      bodies.add(await res.text());
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
    const res = await call(`/accounts/${acc}/repositories/101/backlog`);
    expect(res.status).toBe(200);
    const body = await res.json();
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
    const none = await (await call(`/accounts/${acc}/repositories/101/backlog`)).json();
    expect(none.ranking).toBeNull();
    expect(none.refinements).toEqual([]);
    h.github.findRankingTable = async () => {
      throw new Error("GitHub 502");
    };
    expect((await call(`/accounts/${acc}/repositories/101/backlog`)).status).toBe(503);
  });
});
