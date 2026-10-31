// 実行の制御・取り込み・キーの持ち込みのテストの道具。GitHub・実行基盤・秘密の置き場は偽物で、表と進捗の記録はメモリに置く
import type { RunJobInstruction } from "@ndf-factory/contracts";
import type { AccountContext, ExecutionMode, Plan, SeatType } from "../accounts/model.ts";
import type { GitHubPort, InstallationToken, IssueSummary, RankingTable, ReachableRepository } from "../github/port.ts";
import { appendControlEventTo } from "../progress/append.ts";
import { MemoryProgressEventStore } from "../progress/memory-store.ts";
import { memoryProgressReader } from "../progress/read.ts";
import { RunControl, type RunControlConfig } from "../runs/control.ts";
import { MemoryRunsTable } from "../runs/memory-table.ts";
import type { RunnerPlatform, SignedUrl, TaskStatus } from "../runs/platform.ts";
import { createRunJobTokenKit, type RunJobTokenKit } from "./run-job-tokens.ts";

export class FakeGitHub implements GitHubPort {
  // 導入ごとに届くリポジトリ（数の ID → 名前）
  readonly installations = new Map<number, Map<number, string>>();
  // 利用者の token ごとに届く導入
  readonly userInstallations = new Map<string, number[]>();
  readonly codes = new Map<string, string>();
  readonly openIssues = new Map<string, IssueSummary[]>();
  readonly rankings = new Map<string, RankingTable>();
  readonly tokenRequests: { installationId: number; repositoryId: number }[] = [];

  installUrl() {
    return "https://github.com/apps/ndf-factory-test/installations/new";
  }
  authorizeUrl(state: string) {
    return `https://github.com/login/oauth/authorize?client_id=test&state=${state}`;
  }
  async exchangeCode(code: string) {
    return this.codes.get(code) ?? null;
  }
  async listReachableRepositories(userToken: string): Promise<ReachableRepository[]> {
    return (this.userInstallations.get(userToken) ?? []).flatMap((installationId) =>
      [...(this.installations.get(installationId) ?? new Map()).entries()].map(([repositoryId, fullName]) => ({ repositoryId, fullName, installationId })),
    );
  }
  async createInstallationToken(installationId: number, repositoryId: number): Promise<InstallationToken | null> {
    this.tokenRequests.push({ installationId, repositoryId });
    if (!this.installations.get(installationId)?.has(repositoryId)) return null;
    return { token: `ghs_test_${installationId}_${repositoryId}`, expiresAt: new Date(Date.now() + 3_600_000).toISOString() };
  }
  async repositoryName(installationId: number, repositoryId: number) {
    return this.installations.get(installationId)?.get(repositoryId) ?? null;
  }
  async checkOpenIssues(_installationId: number, _repositoryId: number, fullName: string, numbers: number[]) {
    const open = new Set((this.openIssues.get(fullName) ?? []).map((i) => i.number));
    return { invalid: numbers.filter((n) => !open.has(n)) };
  }
  async listRecentOpenIssues(_installationId: number, _repositoryId: number, fullName: string, limit: number) {
    return (this.openIssues.get(fullName) ?? []).slice(0, limit);
  }
  async findRankingTable(_installationId: number, _repositoryId: number, fullName: string) {
    return this.rankings.get(fullName) ?? null;
  }
}

export class FakePlatform implements RunnerPlatform {
  readonly tokens = new Map<string, string>();
  readonly tasks = new Map<string, { status: TaskStatus; mode: string; instruction: RunJobInstruction; startedBy: string }>();
  readonly stopped: { taskArn: string; reason: string }[] = [];
  readonly states = new Set<string>();
  failRunTask = false;
  private n = 0;

  async putJobToken(jobId: string, token: string) {
    const arn = `arn:aws:secretsmanager:us-east-1:111111111111:secret:ndf-factory/run-jobs/${jobId}`;
    this.tokens.set(arn, token);
    return arn;
  }
  async deleteJobToken(secretArn: string) {
    this.tokens.delete(secretArn);
  }
  async runTask(mode: "byok" | "managed", instruction: RunJobInstruction, startedBy: string) {
    if (this.failRunTask) throw new Error("RunTask failed");
    const arn = `arn:aws:ecs:us-east-1:111111111111:task/runner/${++this.n}`;
    this.tasks.set(arn, { status: "PENDING", mode, instruction, startedBy });
    return arn;
  }
  async stopTask(taskArn: string, reason: string) {
    this.stopped.push({ taskArn, reason });
    const t = this.tasks.get(taskArn);
    if (t) t.status = "STOPPED";
  }
  async describeTask(taskArn: string): Promise<TaskStatus> {
    return this.tasks.get(taskArn)?.status ?? "MISSING";
  }
  async stateUrl(sprintRunId: string, op: "put" | "get"): Promise<SignedUrl | null> {
    if (op === "get" && !this.states.has(sprintRunId)) return null;
    return { url: `https://state.example/sprint-runs/${sprintRunId}/ndf-state.tar.gz?op=${op}`, expiresAt: new Date(Date.now() + 900_000).toISOString() };
  }
  taskOf(jobId: string) {
    return [...this.tasks.entries()].find(([, t]) => t.startedBy === jobId);
  }
}

export const TEST_CONFIG: RunControlConfig = { timeoutSeconds: 28_800, tokenTtlSeconds: 32_400, ingestUrl: "https://api.example/ingest/events" };

export function accountContext(patch: Partial<AccountContext> & { plan?: Plan; seatType?: SeatType | null; executionMode?: ExecutionMode } = {}): AccountContext {
  return {
    accountId: "acc-1",
    userId: "user-1",
    kind: "personal",
    plan: "max",
    role: "owner",
    seatType: null,
    executionMode: "byok",
    ...patch,
  };
}

export interface RunsHarness {
  table: MemoryRunsTable;
  github: FakeGitHub;
  platform: FakePlatform;
  progress: MemoryProgressEventStore;
  tokens: RunJobTokenKit;
  control: RunControl;
  clock: { now: Date };
  logs: Record<string, unknown>[];
}

export async function createRunsHarness(config: RunControlConfig = TEST_CONFIG): Promise<RunsHarness> {
  const table = new MemoryRunsTable();
  const github = new FakeGitHub();
  const platform = new FakePlatform();
  const progress = new MemoryProgressEventStore();
  const tokens = await createRunJobTokenKit();
  const clock = { now: new Date("2026-10-31T00:00:00Z") };
  const logs: Record<string, unknown>[] = [];
  let id = 0;
  const control = new RunControl({
    table,
    github,
    platform,
    reader: memoryProgressReader(progress),
    appendControl: (e) => appendControlEventTo(progress, e),
    signingKey: async () => tokens.signingKey,
    ndfVersion: async () => "10.26.1",
    config,
    now: () => clock.now,
    newId: () => `id${String(++id).padStart(4, "0")}`,
    log: (e) => logs.push(e),
  });
  return { table, github, platform, progress, tokens, control, clock, logs };
}
