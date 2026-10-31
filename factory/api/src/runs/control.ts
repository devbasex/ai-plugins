// 実行の制御（#2186 の F5〜F8）。開始の判定・枠・token の発行・起動・停止の知らせ・見回り・取り消し・再開の要求・渡す口。
// ジョブと実行の状態の移りは、タスクの停止の後（停止の知らせか見回り）だけで行う（決定 13）
import { randomUUID } from "node:crypto";
import type { JobFinishedEvent, RunJobInstruction } from "@ndf-factory/contracts";
import { judgeLimits } from "../accounts/limits.ts";
import type { AccountContext } from "../accounts/model.ts";
import { ConflictError, ForbiddenError, InvalidRequestError, NotFoundError, ServiceUnavailableError } from "../errors.ts";
import type { GitHubPort } from "../github/port.ts";
import { finishedKey } from "../progress/authorize.ts";
import type { ProgressReader } from "../progress/read.ts";
import { signRunJobToken, type RunJobSigningKey } from "../progress/run-job-token.ts";
import { isTerminalJob, isTerminalRun, type FailureReason, type RunJob, type SprintRun } from "./model.ts";
import type { RunnerPlatform } from "./platform.ts";
import type { RunsTable } from "./table.ts";

export const DEFAULT_TIMEOUT_SECONDS = 28_800;
export const DEFAULT_TOKEN_TTL_SECONDS = 32_400;
// pending のまま残ってよい時間（Fargate の起動）
export const PENDING_LIMIT_SECONDS = 15 * 60;
export const MAX_ISSUES = 20;
const SLOT_RETRIES = 3;

export interface RunControlConfig {
  timeoutSeconds: number;
  tokenTtlSeconds: number;
  ingestUrl: string;
}

// 設定を読む。token の期限が打ち切りの時間以下なら断る（I41）
export function runControlConfig(env: Record<string, string | undefined>): RunControlConfig {
  const num = (name: string, fallback: number) => {
    const raw = env[name];
    if (raw === undefined || raw === "") return fallback;
    const n = Number(raw);
    if (!Number.isInteger(n) || n <= 0) throw new Error(`環境変数 ${name} は正の整数にしてください`);
    return n;
  };
  const timeoutSeconds = num("RUN_JOB_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS);
  const tokenTtlSeconds = num("RUN_JOB_TOKEN_TTL_SECONDS", DEFAULT_TOKEN_TTL_SECONDS);
  if (tokenTtlSeconds <= timeoutSeconds) {
    throw new Error("RUN_JOB_TOKEN_TTL_SECONDS は RUN_JOB_TIMEOUT_SECONDS より長くしてください");
  }
  const ingestUrl = env.INGEST_URL;
  if (!ingestUrl) throw new Error("環境変数 INGEST_URL がありません");
  return { timeoutSeconds, tokenTtlSeconds, ingestUrl };
}

export interface RunControlDeps {
  table: RunsTable;
  github: GitHubPort;
  platform: RunnerPlatform;
  reader: ProgressReader;
  appendControl: (event: JobFinishedEvent) => Promise<{ written: boolean }>;
  signingKey: () => Promise<RunJobSigningKey>;
  // 開始の時点のマーケットプレイスの NDF の版（決定 3）
  ndfVersion: () => Promise<string>;
  config: RunControlConfig;
  now?: () => Date;
  newId?: () => string;
  log?: (entry: Record<string, unknown>) => void;
}

export type ResumeRejectedCode = "not_found" | "not_awaiting_approval" | "sprint_run_busy";

export class ResumeRejectedError extends Error {
  constructor(readonly code: ResumeRejectedCode) {
    super(`再開の要求を受けられません（${code}）`);
    this.name = "ResumeRejectedError";
  }
}

export interface StartInput {
  repositoryId: unknown;
  issues: unknown;
}

const RESULT_TO_RUN = { done: "done", stopped: "stopped", failed: "failed" } as const;

export class RunControl {
  private readonly now: () => Date;
  private readonly newId: () => string;
  private readonly log: (entry: Record<string, unknown>) => void;

  constructor(private readonly deps: RunControlDeps) {
    this.now = deps.now ?? (() => new Date());
    this.newId = deps.newId ?? (() => randomUUID().replaceAll("-", ""));
    this.log = deps.log ?? ((entry) => console.log(JSON.stringify(entry)));
  }

  private iso(): string {
    return this.now().toISOString();
  }

  // 開始。確かめの順は 課題の形（400）→ 登録（404）→ 課題（400）→ キー（409）→ 導入（409）→ 使える量（409）
  async startSprintRun(ctx: AccountContext, input: StartInput): Promise<{ sprint_run_id: string; state: "starting" }> {
    const { table, github } = this.deps;
    const repositoryId = input.repositoryId;
    if (!Number.isInteger(repositoryId) || (repositoryId as number) < 1) throw new InvalidRequestError("リポジトリの ID の形ではありません");
    const issues = input.issues;
    if (
      !Array.isArray(issues) ||
      issues.length < 1 ||
      issues.length > MAX_ISSUES ||
      !issues.every((n) => Number.isInteger(n) && n >= 1) ||
      new Set(issues).size !== issues.length
    ) {
      throw new InvalidRequestError(`課題は 1〜${MAX_ISSUES} 件の番号で選んでください`, "invalid_issues", { numbers: [] });
    }
    const registration = await table.getRegistration(ctx.accountId, repositoryId as number);
    if (!registration) throw new NotFoundError("repository_not_found", "登録したリポジトリが見つかりません");
    const { invalid } = await github.checkOpenIssues(registration.installationId, registration.fullName, issues as number[]);
    if (invalid.length > 0) throw new InvalidRequestError("開いている課題ではありません", "invalid_issues", { numbers: invalid });

    let modelCredentialSecretArn: string | null = null;
    if (ctx.executionMode === "byok") {
      const cred = await table.getCredential(ctx.accountId);
      if (!cred) throw new ConflictError("model_credential_missing", "キーの持ち込みを先に保存してください");
      modelCredentialSecretArn = cred.secretArn;
    }
    const fullName = await github.repositoryName(registration.installationId, registration.repositoryId);
    if (!fullName) throw new ConflictError("installation_unreachable", "GitHub App の導入がこのリポジトリへ届きません");
    if (fullName !== registration.fullName) await table.setRegistrationName(ctx.accountId, registration.repositoryId, fullName);
    const ndfVersion = await this.deps.ndfVersion();

    const now = this.iso();
    const run: SprintRun = {
      sprintRunId: this.newId(),
      accountId: ctx.accountId,
      repositoryId: registration.repositoryId,
      repo: fullName,
      state: "starting",
      issues: issues as number[],
      ndfVersion,
      executionMode: ctx.executionMode,
      modelCredentialSecretArn,
      currentJobId: null,
      jobCount: 1,
      cancelRequested: false,
      failureReason: null,
      createdBy: ctx.userId,
      createdAt: now,
      updatedAt: now,
    };
    const job = this.newJob(run, 1, "new", null);
    run.currentJobId = job.jobId;

    for (let attempt = 0; attempt < SLOT_RETRIES; attempt++) {
      const slots = await table.getSlots(ctx.accountId, registration.repositoryId);
      const tokensThisMonth = ctx.executionMode === "managed" ? await this.tokensThisMonth(ctx.accountId) : undefined;
      const judgment = judgeLimits(ctx, { runningSprintRuns: slots.account, tokensThisMonth });
      if (!judgment.allowed) {
        throw new ConflictError("limits_exceeded", "使える量の上限に達しています", { reasons: judgment.reasons });
      }
      if (await table.createRun(run, job, slots)) {
        await this.launch(run, job);
        return { sprint_run_id: run.sprintRunId, state: "starting" };
      }
    }
    throw new ServiceUnavailableError("sprint_run_unavailable");
  }

  // その UTC の暦月に、アカウントの実行が使った token（形態 managed の上限の判定に使う）
  private async tokensThisMonth(accountId: string): Promise<number> {
    const now = this.now();
    const since = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
    const runs = await this.deps.table.listRuns(accountId, 1000);
    let total = 0;
    for (const run of runs) {
      if (isTerminalRun(run.state) && new Date(run.updatedAt) < since) continue;
      total += await this.deps.reader.sumTokensSince(run.sprintRunId, since);
    }
    return total;
  }

  private newJob(run: SprintRun, seq: number, start: "new" | "resume", approvalRequestId: string | null): RunJob {
    return {
      jobId: this.newId(),
      sprintRunId: run.sprintRunId,
      accountId: run.accountId,
      seq,
      start,
      approvalRequestId,
      state: "pending",
      runnerKind: run.executionMode === "local" ? "local" : "ecs",
      taskArn: null,
      tokenSecretArn: null,
      stopReason: null,
      createdAt: this.iso(),
      startedAt: null,
      finishedAt: null,
      result: null,
    };
  }

  // 実行ジョブの指示。形は受け口の仕様の「実行ジョブの指示の定義」のまま、値の出所だけを決める
  buildInstruction(run: SprintRun, job: RunJob, tokenSecretArn: string): RunJobInstruction {
    const instruction: RunJobInstruction = {
      SPRINT_RUN_ID: run.sprintRunId,
      JOB_ID: job.jobId,
      REPO: run.repo,
      NDF_VERSION: run.ndfVersion,
      START: job.start,
      INGEST_URL: this.deps.config.ingestUrl,
      INGEST_TOKEN_SECRET_ARN: tokenSecretArn,
      EXECUTION_MODE: run.executionMode,
    };
    if (job.start === "new") instruction.ISSUES = run.issues.join(",");
    if (job.approvalRequestId) instruction.APPROVAL_REQUEST_ID = job.approvalRequestId;
    if (run.executionMode === "byok" && run.modelCredentialSecretArn) instruction.MODEL_CREDENTIAL_SECRET_ARN = run.modelCredentialSecretArn;
    return instruction;
  }

  // ジョブの token を発行する。期限は打ち切りの時間より長い（I41）
  private async issueToken(run: SprintRun, job: RunJob): Promise<string> {
    const expiresAt = new Date(this.now().getTime() + this.deps.config.tokenTtlSeconds * 1000);
    return signRunJobToken({ jobId: job.jobId, sprintRunId: run.sprintRunId, repo: run.repo, expiresAt }, await this.deps.signingKey());
  }

  // 起動。local は渡す口へ積むだけ（#2189 が claimLocalRunJob で受け取る）。起動できなければジョブと実行を failed にする
  private async launch(run: SprintRun, job: RunJob): Promise<void> {
    if (job.runnerKind === "local") return;
    const { platform, table } = this.deps;
    let tokenSecretArn: string | null = null;
    try {
      tokenSecretArn = await platform.putJobToken(job.jobId, await this.issueToken(run, job));
      await table.updateJob(job.jobId, { tokenSecretArn });
      const mode = run.executionMode === "byok" ? "byok" : "managed";
      const taskArn = await platform.runTask(mode, this.buildInstruction(run, job, tokenSecretArn), job.jobId);
      await table.updateJob(job.jobId, { taskArn });
    } catch (err) {
      this.log({ event: "launch_failed", sprint_run_id: run.sprintRunId, job_id: job.jobId, error: (err as Error).name });
      await this.finalize({ ...job, tokenSecretArn }, "launch_failed");
    }
  }

  // 停止の知らせ（EventBridge の ECS のタスクの状態の変化）。startedBy のジョブの ID から引く
  async onTaskStateChange(jobId: string, status: "RUNNING" | "STOPPED"): Promise<void> {
    const job = await this.deps.table.getJob(jobId);
    if (!job || isTerminalJob(job.state)) return;
    if (status === "RUNNING") {
      await this.markRunning(job);
      return;
    }
    await this.finalize(job, job.stopReason ?? "task_stopped");
  }

  private async markRunning(job: RunJob): Promise<boolean> {
    const now = this.iso();
    const ok = await this.deps.table.updateJob(job.jobId, { state: "running", startedAt: now }, ["pending"]);
    if (ok) await this.deps.table.markRunRunning(job.sprintRunId, now);
    return ok;
  }

  // タスクが止まった後の処理。job.finished を読み、無ければ control で failed を書いてから結果に従って移す（I40）
  private async finalize(job: RunJob, reason: FailureReason): Promise<void> {
    const { table, reader, platform } = this.deps;
    const run = await table.getRun(job.sprintRunId);
    if (!run) return;
    let finished = await reader.findJobFinished(run.sprintRunId, job.jobId);
    if (!finished) {
      const event: JobFinishedEvent = {
        sprint_run_id: run.sprintRunId,
        idempotency_key: finishedKey(job.jobId),
        type: "job.finished",
        at: this.iso(),
        source: "control",
        job_id: job.jobId,
        schema_version: 1,
        payload: { result: "failed", summary: `実行の制御が終わりを書いた（${reason}）` },
      };
      const { written } = await this.deps.appendControl(event);
      finished = written ? event : ((await reader.findJobFinished(run.sprintRunId, job.jobId)) ?? event);
    }
    const result = finished.payload.result;
    const now = this.iso();
    const updated = await table.updateJob(job.jobId, { state: result, result, finishedAt: now }, ["pending", "running"]);
    if (!updated) return;
    if (job.tokenSecretArn) {
      await platform.deleteJobToken(job.tokenSecretArn).catch((err: Error) =>
        this.log({ event: "token_delete_failed", job_id: job.jobId, error: err.name }),
      );
    }
    if (run.cancelRequested) {
      await table.finishRun(run.sprintRunId, "cancelled", null, now);
    } else if (result === "gate") {
      await table.awaitApproval(run.sprintRunId, job.jobId, now);
    } else {
      const failure = result === "failed" ? (job.stopReason ?? (finished.source === "control" ? reason : "job_failed")) : null;
      await table.finishRun(run.sprintRunId, RESULT_TO_RUN[result], failure, now);
    }
  }

  // 見回り（5 分ごと）。打ち切りの時間を過ぎたジョブを止め、止まったタスクと起動しないジョブを終わらせる（I8）
  async sweep(): Promise<void> {
    const { table, platform, config } = this.deps;
    const now = this.now().getTime();
    for (const job of await table.listActiveJobs()) {
      const startedAt = job.startedAt ? new Date(job.startedAt).getTime() : null;
      const overTime = startedAt !== null && now - startedAt > config.timeoutSeconds * 1000;
      if (job.runnerKind === "local") {
        if (job.state === "running" && overTime) await this.finalize({ ...job, stopReason: "timeout" }, "timeout");
        continue;
      }
      if (job.state === "pending" && now - new Date(job.createdAt).getTime() > PENDING_LIMIT_SECONDS * 1000) {
        if (job.taskArn) await platform.stopTask(job.taskArn, "launch_failed").catch(() => undefined);
        await this.finalize({ ...job, stopReason: "launch_failed" }, "launch_failed");
        continue;
      }
      if (!job.taskArn) continue;
      const status = await platform.describeTask(job.taskArn);
      if (status === "STOPPED" || status === "MISSING") {
        await this.finalize(job, job.stopReason ?? "task_stopped");
      } else if (job.state === "running" && overTime && job.stopReason === null) {
        await table.updateJob(job.jobId, { stopReason: "timeout" });
        await platform.stopTask(job.taskArn, "timeout");
      } else if (status === "RUNNING" && job.state === "pending") {
        await this.markRunning(job);
      }
    }
  }

  // 再開の要求（#2173）。承認待ちで動くジョブが無いときだけ resume のジョブを作り起動する（I8・I9）
  async requestResume(input: { sprintRunId: string; approvalRequestId: string }): Promise<{ jobId: string }> {
    const { table, reader } = this.deps;
    const run = await table.getRun(input.sprintRunId);
    if (!run) throw new ResumeRejectedError("not_found");
    if (run.state !== "awaiting_approval") {
      if (!isTerminalRun(run.state) && run.currentJobId) {
        const finished = await reader.findJobFinished(run.sprintRunId, run.currentJobId);
        if (finished?.payload.result === "gate") throw new ResumeRejectedError("sprint_run_busy");
      }
      throw new ResumeRejectedError("not_awaiting_approval");
    }
    if (run.currentJobId !== null) throw new ResumeRejectedError("sprint_run_busy");
    const seq = run.jobCount + 1;
    const job = this.newJob(run, seq, "resume", input.approvalRequestId);
    if (!(await table.addResumeJob(job, this.iso()))) {
      const now = await table.getRun(run.sprintRunId);
      throw new ResumeRejectedError(now?.state === "awaiting_approval" || now?.state === "starting" ? "sprint_run_busy" : "not_awaiting_approval");
    }
    await this.launch(run, job);
    return { jobId: job.jobId };
  }

  // 取り消し。動くジョブがあれば止め、停止の後に cancelled へ移す。無ければその場で cancelled にする
  async cancelSprintRun(ctx: AccountContext, sprintRunId: string): Promise<{ state: "cancelled" | "running" }> {
    const { table, platform } = this.deps;
    const run = await table.getRun(sprintRunId);
    if (!run || run.accountId !== ctx.accountId) throw new NotFoundError("sprint_run_not_found", "スプリントの実行が見つかりません");
    if (isTerminalRun(run.state)) throw new ConflictError("already_finished", "このスプリントの実行は終わっています");
    if (run.currentJobId === null) {
      if (await table.finishRun(run.sprintRunId, "cancelled", null, this.iso())) return { state: "cancelled" };
      throw new ConflictError("already_finished", "このスプリントの実行は終わっています");
    }
    await table.setCancelRequested(run.sprintRunId);
    const job = await table.getJob(run.currentJobId);
    if (!job || isTerminalJob(job.state)) return { state: "running" };
    if (job.runnerKind === "local" || !job.taskArn) {
      await this.finalize(job, "task_stopped");
      return { state: "cancelled" };
    }
    await platform.stopTask(job.taskArn, "cancelled");
    return { state: "running" };
  }

  async listSprintRuns(ctx: AccountContext, repositoryId: number | null) {
    const runs = await this.deps.table.listRuns(ctx.accountId, repositoryId === null ? 50 : 1000);
    return runs
      .filter((r) => repositoryId === null || r.repositoryId === repositoryId)
      .slice(0, 50)
      .map((r) => ({
        sprint_run_id: r.sprintRunId,
        repository_id: r.repositoryId,
        issues: r.issues,
        state: r.state,
        failure_reason: r.failureReason,
        created_at: r.createdAt,
      }));
  }

  // 形態 local の渡す口（#2189）。最も古い pending の local のジョブを running にし、指示と token を 1 回だけ返す
  async claimLocalRunJob(accountId: string): Promise<{ instruction: RunJobInstruction; token: string } | null> {
    const { table } = this.deps;
    for (const job of await table.listActiveJobs()) {
      if (job.runnerKind !== "local" || job.state !== "pending" || job.accountId !== accountId) continue;
      const run = await table.getRun(job.sprintRunId);
      if (!run || run.cancelRequested) continue;
      if (!(await this.markRunning(job))) continue;
      const token = await this.issueToken(run, job);
      // local の token は置き場に書かない。指示の形を保つため、置き場の項目には local を表す ARN を置く
      const instruction = this.buildInstruction(run, job, `arn:aws:secretsmanager:local:000000000000:secret:ndf-factory/run-jobs/${job.jobId}`);
      return { instruction, token };
    }
    return null;
  }

  // 手元のランナーの終わりか心拍の切れ（#2189）。停止の知らせと同じ処理
  async reportLocalRunJobEnded(jobId: string): Promise<void> {
    const job = await this.deps.table.getJob(jobId);
    if (!job || job.runnerKind !== "local" || isTerminalJob(job.state)) return;
    await this.finalize(job, "task_stopped");
  }

  // ランナー向けの API が token のジョブを確かめる。終わりの状態のジョブ・別の実行は 403
  async activeJobFor(jobId: string, sprintRunId: string): Promise<{ run: SprintRun; job: RunJob }> {
    const job = await this.deps.table.getJob(jobId);
    if (!job || job.sprintRunId !== sprintRunId || isTerminalJob(job.state)) throw new ForbiddenError();
    const run = await this.deps.table.getRun(sprintRunId);
    if (!run) throw new ForbiddenError();
    return { run, job };
  }
}
