// 実行の制御（#2186 の F5〜F8、#2347 の F3・F7・F10）。開始の判定・枠・持ち分・token の発行・起動・停止の知らせ・見回り・
// 取り消し・再開の要求・渡す口。ジョブと実行の状態の移りは、タスクの停止の後（停止の知らせか見回り）だけで行う（決定 13）。
// 開発ループの行の中身はループの制御（loops.ts）の関数が組み、ここは実行と同じトランザクションへ入れるだけにする（#2347 の決定 2）
import { randomUUID } from "node:crypto";
import type { JobFinishedEvent, RunJobInstruction } from "@ndf-factory/contracts";
import { judgeLimits } from "../accounts/limits.ts";
import type { AccountContext } from "../accounts/model.ts";
import { ConflictError, ForbiddenError, InvalidRequestError, NotFoundError, ServiceUnavailableError } from "../errors.ts";
import type { GitHubPort } from "../github/port.ts";
import type { OrgRequest } from "../organizer/model.ts";
import { finishedKey } from "../progress/authorize.ts";
import type { ProgressReader, ReleaseEvents } from "../progress/read.ts";
import { signRunJobToken, type RunJobSigningKey } from "../progress/run-job-token.ts";
import { loopWriteAfterRun, loopWriteOnResume, type RunOutcome } from "./loops.ts";
import {
  isReleaseRun,
  isTerminalJob,
  isTerminalLoop,
  isTerminalRun,
  type CreateRunGuards,
  type DevLoop,
  type FailureReason,
  type LoopJobInput,
  type LoopWrite,
  type FinishReason,
  type Registration,
  type ReleaseStep,
  type RunJob,
  type SprintRun,
} from "./model.ts";
import type { RunnerPlatform } from "./platform.ts";
import type { RunsTable } from "./table.ts";
import { isLegacyVersion } from "./version.ts";

export const DEFAULT_TIMEOUT_SECONDS = 28_800;
export const DEFAULT_TOKEN_TTL_SECONDS = 32_400;
// リセット時刻を読めないときの待ち（NDF の supervise の LIMIT_RETRY と同じ。#2347 の決定 9）
export const DEFAULT_USAGE_LIMIT_RETRY_SECONDS = 900;
// pending のまま残ってよい時間（Fargate の起動）
export const PENDING_LIMIT_SECONDS = 15 * 60;
export const MAX_ISSUES = 20;
const SLOT_RETRIES = 3;

export interface RunControlConfig {
  timeoutSeconds: number;
  tokenTtlSeconds: number;
  ingestUrl: string;
  // 並行を許す NDF の版の下限（#2347 の決定 12）。無ければ並行を許さない
  parallelMinVersion?: string | null;
  // local のループも自動で次の周回を起こす（I52）。既定 false
  localLoopAutostart?: boolean;
  usageLimitRetrySeconds?: number;
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
  return {
    timeoutSeconds,
    tokenTtlSeconds,
    ingestUrl,
    parallelMinVersion: env.NDF_PARALLEL_MIN_VERSION || null,
    localLoopAutostart: env.LOCAL_LOOP_AUTOSTART === "true",
    usageLimitRetrySeconds: num("USAGE_LIMIT_RETRY_SECONDS", DEFAULT_USAGE_LIMIT_RETRY_SECONDS),
  };
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
  // 自動で起こすジョブ（リセット時刻の後の再開）の前に、実行を始めた利用者の今の文脈を引く。席が無ければ null
  resolveContext?: (accountId: string, userId: string) => Promise<AccountContext | null>;
  now?: () => Date;
  newId?: () => string;
  log?: (entry: Record<string, unknown>) => void;
}

// ループの制御が渡す口（停止の後と見回りで次の周回を起こす）。循環を避けるため、組み立ての後に差し込む
export interface LoopHooks {
  startNextLap(accountId: string, loopId: string): Promise<void>;
  wakeWaitingLoops(accountId: string): Promise<void>;
  sweepLoops(): Promise<void>;
}

// Factory のオーガナイザーが渡す口（#2439）。循環を避けるため、組み立ての後に差し込む
export interface OrganizerHooks {
  newRequestRows(run: SprintRun, events: ReleaseEvents): Promise<OrgRequest[]>;
  tick(accountId: string, repositoryId: number): Promise<void>;
  decide(run: SprintRun, approvalRequestId: string): Promise<ReleaseStep | null>;
  sweep(): Promise<void>;
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

// 手元のマシンの申告（#2189 の登録と心拍。契約の local-runner-declaration）
export interface LocalRunner {
  runnerId: string;
  maxJobs?: number;
}

const RESULT_TO_RUN = { done: "done", stopped: "stopped", failed: "failed" } as const;

// 取り消し・gate・local の利用上限のどれでもない終わりの、実行の状態と失敗の理由。
// local 以外の利用上限は failed（job_failed）。failed のとき理由はジョブの停止の理由 → control が書いた終わりなら reason → job_failed の順
function runEndOf(
  result: keyof typeof RESULT_TO_RUN,
  finishReason: string | null,
  job: RunJob,
  finished: JobFinishedEvent,
  reason: FailureReason,
): { state: (typeof RESULT_TO_RUN)[keyof typeof RESULT_TO_RUN]; failure: FailureReason | null } {
  if (result === "stopped" && finishReason === "usage_limit") return { state: "failed", failure: "job_failed" };
  const state = RESULT_TO_RUN[result];
  if (state !== "failed") return { state, failure: null };
  return { state, failure: job.stopReason ?? (finished.source === "control" ? reason : "job_failed") };
}

export class RunControl {
  private readonly now: () => Date;
  private readonly newId: () => string;
  private readonly log: (entry: Record<string, unknown>) => void;
  private hooks: LoopHooks | null = null;
  private organizer: OrganizerHooks | null = null;

  constructor(private readonly deps: RunControlDeps) {
    this.now = deps.now ?? (() => new Date());
    this.newId = deps.newId ?? (() => randomUUID().replaceAll("-", ""));
    this.log = deps.log ?? ((entry) => console.log(JSON.stringify(entry)));
  }

  setLoopHooks(hooks: LoopHooks): void {
    this.hooks = hooks;
  }

  setOrganizer(organizer: OrganizerHooks): void {
    this.organizer = organizer;
  }

  get config(): RunControlConfig {
    return this.deps.config;
  }

  private iso(): string {
    return this.now().toISOString();
  }

  // 開始。確かめの順は 課題の形（400）→ 登録（404）→ 課題（400）→ ループのマイルストーン（409）→ キー（409）→ 導入（409）→
  // 使える量（409）→ 版（409）→ 課題の持ち分（409）
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
    const check = await github.checkOpenIssues(registration.installationId, registration.repositoryId, registration.fullName, issues as number[]);
    if (check.invalid.length > 0) throw new InvalidRequestError("開いている課題ではありません", "invalid_issues", { numbers: check.invalid });
    // 動いているループのマイルストーンに属する課題は断る（ループの周回の選定と取り合う。決定 11）
    const titles = [...new Set(Object.values(check.milestones ?? {}).filter((t): t is string => typeof t === "string" && t !== ""))];
    await this.refuseIssuesInLoop(ctx.accountId, registration.repositoryId, titles, check.milestones ?? {});

    const created = await this.admit(ctx, registration, {
      issues: issues as number[],
      milestoneTitles: titles,
      milestonesOf: check.milestones ?? {},
      start: "new",
    });
    if (created === "loop_changed") throw new ServiceUnavailableError("sprint_run_unavailable");
    return { sprint_run_id: created.sprintRunId, state: "starting" };
  }

  private async refuseIssuesInLoop(accountId: string, repositoryId: number, titles: string[], milestonesOf: Record<number, string | null>): Promise<void> {
    for (const title of titles) {
      const share = await this.deps.table.getMilestoneShare(accountId, repositoryId, title);
      if (share) await this.throwIssueInLoop(accountId, share.loopId, title, milestonesOf);
    }
  }

  private async throwIssueInLoop(accountId: string, loopId: string, title: string, milestonesOf: Record<number, string | null>): Promise<never> {
    const loop = await this.deps.table.getLoop(accountId, loopId);
    const numbers = Object.entries(milestonesOf)
      .filter(([, t]) => t === title)
      .map(([n]) => Number(n));
    throw new ConflictError("issue_in_loop", `課題はループ ${loop?.name ?? loopId} が選定の対象にしているマイルストーンにあります`, {
      numbers,
      loop_name: loop?.name ?? null,
    });
  }

  // 並行を許さない古い版として数えるか（決定 12）。移行の済みの行が無い間は、版によらず古い版として扱う
  private async isLegacy(ndfVersion: string): Promise<boolean> {
    if (!(await this.deps.table.isMigrated())) return true;
    return isLegacyVersion(ndfVersion, this.deps.config.parallelMinVersion);
  }

  // 開始と周回の開始に共通の判定と書き込み。断るときは ApiError を投げる。ループの行が変わっていれば loop_changed
  private async admit(
    ctx: AccountContext,
    registration: Registration,
    opts: {
      issues: number[];
      milestoneTitles: string[];
      milestonesOf: Record<number, string | null>;
      start: "new" | "loop";
      loop?: { loop: DevLoop; write: (run: SprintRun) => LoopWrite };
    },
  ): Promise<SprintRun | "loop_changed"> {
    const { table, github } = this.deps;
    let modelCredentialSecretArn: string | null = null;
    if (ctx.executionMode === "byok") {
      const cred = await table.getCredential(ctx.accountId);
      if (!cred) throw new ConflictError("model_credential_missing", "キーの持ち込みを先に保存してください");
      modelCredentialSecretArn = cred.secretArn;
    }
    // 周回の開始は、枠待ちのループを見回りと実行の終わりが起こし直すたびに通る。表だけで決まる断り（同時の数と古い版）を
    // GitHub・配布元・月の token の数えより先に判定する（断るループに外の呼び出しを走らせない）
    if (opts.start === "loop") await this.refuseFullSlots(ctx, registration);
    const fullName = await github.repositoryName(registration.installationId, registration.repositoryId);
    if (!fullName) throw new ConflictError("installation_unreachable", "GitHub App の導入がこのリポジトリへ届きません");
    if (fullName !== registration.fullName) await table.setRegistrationName(ctx.accountId, registration.repositoryId, fullName);
    const ndfVersion = await this.deps.ndfVersion();
    // 移行の済みの行はトランザクションの前に 1 回だけ読む（全アカウントで共有の項目を照合に入れない。決定 12）
    const legacy = await this.isLegacy(ndfVersion);

    const now = this.iso();
    const loop = opts.loop?.loop ?? null;
    const run: SprintRun = {
      sprintRunId: this.newId(),
      accountId: ctx.accountId,
      repositoryId: registration.repositoryId,
      repo: fullName,
      state: "starting",
      issues: opts.issues,
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
      ...(loop ? { loopId: loop.loopId, lap: loop.lapRuns + 1 } : {}),
    };
    const job = this.newJob(run, 1, opts.start, null);
    run.currentJobId = job.jobId;

    // 月の token は枠の取り直しで変わらないため、1 回の開始で 1 度だけ数える
    const tokensThisMonth = ctx.executionMode === "managed" ? await this.tokensThisMonth(ctx.accountId, ctx.plan === "team" ? ctx.userId : null) : undefined;
    for (let attempt = 0; attempt < SLOT_RETRIES; attempt++) {
      const slots = await table.getSlots(ctx.accountId, registration.repositoryId);
      const judgment = judgeLimits(ctx, {
        runningSprintRuns: slots.account,
        tokensThisMonth,
        runningRepoRuns: slots.repository,
        repoMaxRuns: registration.maxConcurrentRuns ?? null,
      });
      if (!judgment.allowed) {
        throw new ConflictError("limits_exceeded", "使える量の上限に達しています", { reasons: judgment.reasons });
      }
      if ((legacy && slots.repository > 0) || (!legacy && (slots.legacy ?? 0) > 0)) {
        throw new ConflictError("parallel_unsupported_version", "このリポジトリでは、並行を許さない NDF の版の実行と並べて動かせません");
      }
      if (loop && opts.loop) {
        // EXCLUDE_ISSUES は、トランザクションの直前に強い整合で読んだ同じリポジトリの課題の持ち分（決定 11）
        const exclude = (await table.listIssueShares(ctx.accountId, registration.repositoryId)).map((s) => s.number);
        job.loopInput = loopInput(loop, exclude);
      }
      const guards: CreateRunGuards = {
        issues: opts.loop ? [] : opts.issues,
        milestoneChecks: opts.milestoneTitles,
        legacy,
        ...(opts.loop ? { loop: opts.loop.write(run) } : {}),
      };
      const result = await table.createRun(run, job, slots, guards);
      if (result === "created") {
        await this.launch({ ...run, legacy }, job);
        return { ...run, legacy };
      }
      if (result === "loop_changed") return "loop_changed";
      // 古い版の実行は、まとめた配布が流れている間は始めない（#2439 の I62）
      if (result === "release_running") {
        throw new ConflictError("parallel_unsupported_version", "このリポジトリでは、並行を許さない NDF の版の実行と並べて動かせません");
      }
      if (result === "issue_in_use") {
        const held = new Set((await table.listIssueShares(ctx.accountId, registration.repositoryId)).map((s) => s.number));
        throw new ConflictError("issue_in_use", "課題は同じリポジトリの別のスプリントの実行が持っています", { numbers: opts.issues.filter((n) => held.has(n)) });
      }
      if (result === "issue_in_loop") {
        await this.refuseIssuesInLoop(ctx.accountId, registration.repositoryId, opts.milestoneTitles, opts.milestonesOf);
      }
    }
    throw new ServiceUnavailableError("sprint_run_unavailable");
  }

  // 版によらず断ると表だけで決まる場合に、admit の枠の判定と同じ断りを投げる。月の token は数えない（同時の数の理由だけを見る）。
  // 古い版の実行がリポジトリにあるか、移行の前（版によらず古い版）で実行があれば、版を読む前に断れる
  private async refuseFullSlots(ctx: AccountContext, registration: Registration): Promise<void> {
    const slots = await this.deps.table.getSlots(ctx.accountId, registration.repositoryId);
    const reasons = judgeLimits(ctx, {
      runningSprintRuns: slots.account,
      runningRepoRuns: slots.repository,
      repoMaxRuns: registration.maxConcurrentRuns ?? null,
    }).reasons.filter((r) => r.quantity.startsWith("concurrent_"));
    if (reasons.length > 0) throw new ConflictError("limits_exceeded", "使える量の上限に達しています", { reasons });
    if ((slots.legacy ?? 0) > 0 || (slots.repository > 0 && !(await this.deps.table.isMigrated()))) {
      throw new ConflictError("parallel_unsupported_version", "このリポジトリでは、並行を許さない NDF の版の実行と並べて動かせません");
    }
  }

  // 開発ループの周回の実行を起こす（ループの制御が呼ぶ）。断る理由は ApiError で返す
  async startLapRun(loop: DevLoop, write: (run: SprintRun) => LoopWrite): Promise<SprintRun | "loop_changed"> {
    const registration = await this.deps.table.getRegistration(loop.accountId, loop.repositoryId);
    if (!registration) throw new NotFoundError("repository_not_found", "登録したリポジトリが見つかりません");
    const ctx: AccountContext = { accountId: loop.accountId, ...loop.context };
    return this.admit(ctx, registration, { issues: [], milestoneTitles: [], milestonesOf: {}, start: "loop", loop: { loop, write } });
  }

  // その UTC の暦月に、アカウントの managed の実行が使った token（形態 managed の上限の判定に使う）。
  // チームでは上限が席ごとのため、userId を渡してその席の利用者が始めた実行だけを数える
  private async tokensThisMonth(accountId: string, userId: string | null): Promise<number> {
    const now = this.now();
    const since = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
    const runs = await this.deps.table.listRuns(accountId, 1000);
    let total = 0;
    for (const run of runs) {
      // 上限は managed（サービス側のモデル）の分だけに効く。local・byok で使った分は数えない
      if (run.executionMode !== "managed") continue;
      if (userId !== null && run.createdBy !== userId) continue;
      if (isTerminalRun(run.state) && new Date(run.updatedAt) < since) continue;
      total += await this.deps.reader.sumTokensSince(run.sprintRunId, since);
    }
    return total;
  }

  private newJob(run: SprintRun, seq: number, start: RunJob["start"], approvalRequestId: string | null): RunJob {
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
    if (job.start === "release") {
      instruction.RELEASE_SEQ = String(run.releaseSeq ?? 1);
      instruction.RELEASE_STEP = job.releaseStep ?? "present";
    }
    if (job.releaseRequestId) instruction.RELEASE_REQUEST_ID = job.releaseRequestId;
    if (run.executionMode === "byok" && run.modelCredentialSecretArn) instruction.MODEL_CREDENTIAL_SECRET_ARN = run.modelCredentialSecretArn;
    const loop = job.loopInput;
    if (loop) {
      instruction.LOOP_NAME = loop.name;
      if (job.start === "loop") {
        instruction.LOOP_MILESTONES = loop.milestones.join(",");
        const budget = (["laps", "usd", "hours"] as const).filter((k) => loop.budget[k] !== null).map((k) => `${k}=${loop.budget[k]}`);
        if (budget.length > 0) instruction.LOOP_BUDGET = budget.join(",");
      }
      if (loop.exclude.length > 0) instruction.EXCLUDE_ISSUES = loop.exclude.join(",");
      if (loop.answers.length > 0) instruction.LOOP_ANSWERS = loop.answers.map((a) => `${a.number}=${a.decision}${a.pr ? `#${a.pr}` : ""}`).join(",");
    }
    return instruction;
  }

  // ジョブの token を発行する。期限は打ち切りの時間より長い（I41）
  private async issueToken(run: SprintRun, job: RunJob): Promise<string> {
    const expiresAt = new Date(this.now().getTime() + this.deps.config.tokenTtlSeconds * 1000);
    return signRunJobToken({ jobId: job.jobId, sprintRunId: run.sprintRunId, repo: run.repo, expiresAt }, await this.deps.signingKey());
  }

  // Factory のオーガナイザーが配布の実行を起こす（組んだ直後と、起動の前で止まったときの起こし直し。#2439）
  launchJob(run: SprintRun, job: RunJob): Promise<void> {
    return this.launch(run, job);
  }

  // まとめた配布の配布の実行とその最初のジョブ（#2439 の I57・I64）。実行の枠を持たず、使える量の判定にかけない。
  // 形態・キー・始めた利用者は範囲の実行（base）から写す
  buildReleaseRun(base: SprintRun, seq: number, ndfVersion: string): { run: SprintRun; job: RunJob } {
    const now = this.iso();
    const run: SprintRun = {
      sprintRunId: this.newId(),
      accountId: base.accountId,
      repositoryId: base.repositoryId,
      repo: base.repo,
      state: "starting",
      issues: [],
      ndfVersion,
      executionMode: base.executionMode,
      modelCredentialSecretArn: base.modelCredentialSecretArn,
      currentJobId: null,
      jobCount: 1,
      cancelRequested: false,
      failureReason: null,
      createdBy: base.createdBy,
      createdAt: now,
      updatedAt: now,
      legacy: false,
      purpose: "release",
      releaseSeq: seq,
    };
    const job = this.newJob(run, 1, "release", null);
    job.releaseStep = "present";
    run.currentJobId = job.jobId;
    return { run, job };
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

  // ループの実行なら、ジョブが送った loop.* を読み、ループの行への書き込みを組む（決定 4）
  private async loopWriteFor(run: SprintRun, jobId: string | null, outcome: Omit<RunOutcome, "laps" | "stopped">): Promise<{ write: LoopWrite; loop: DevLoop } | null> {
    if (!run.loopId) return null;
    const loop = await this.deps.table.getLoop(run.accountId, run.loopId);
    if (!loop || isTerminalLoop(loop.state)) return null;
    const events = jobId ? await this.deps.reader.findLoopEvents(run.sprintRunId, jobId) : { laps: [], stopped: null };
    return { loop, write: loopWriteAfterRun(loop, run, { ...outcome, ...events }, this.iso()) };
  }

  // 実行の終わりへの移り（とループの写し）の後に、次の周回と待っているループを起こす。どちらも落ちてよい（見回りが起こし直す）
  private async afterRunMoved(run: SprintRun, loopWrite: LoopWrite | undefined, slotFreed: boolean): Promise<void> {
    if (!this.hooks) return;
    try {
      if (loopWrite?.set.state === "between_laps" && run.loopId) await this.hooks.startNextLap(run.accountId, run.loopId);
      if (slotFreed) await this.hooks.wakeWaitingLoops(run.accountId);
    } catch (err) {
      this.log({ event: "loop_wake_failed", sprint_run_id: run.sprintRunId, error: (err as Error).name });
    }
  }

  // タスクが止まった後の処理。job.finished を読み、無ければ control で failed を書いてから結果に従って移す（I40）
  private async finalize(job: RunJob, reason: FailureReason): Promise<void> {
    const { table, platform } = this.deps;
    const run = await table.getRun(job.sprintRunId);
    if (!run) return;
    const finished = await this.ensureFinished(run, job, reason);
    const result = finished.payload.result;
    const rawReason = finished.payload.reason ?? null;
    // release_requested は gate の理由（手動の実行が配布の依頼の答えを待つ。#2439 の I65）で、ジョブの終わりの理由には写さない
    const finishReason: FinishReason | null = rawReason === "release_requested" ? null : rawReason;
    const now = this.iso();
    // 配布の依頼と配布の実行の事象（#2439）。受けた依頼の行は実行の移りと同じトランザクションで書く（I59）
    const releaseEvents = this.organizer ? await this.deps.reader.findReleaseEvents(run.sprintRunId, job.jobId) : null;
    const requests = releaseEvents && this.organizer && !isReleaseRun(run) ? await this.organizer.newRequestRows(run, releaseEvents) : [];
    const lastRequest = releaseEvents?.requested.at(-1)?.request_id ?? null;
    // 実行と枠を先に移し、ジョブを終わりの状態にするのは最後にする。途中で止まってもジョブは見回りの対象に残り、
    // 次の見回りか停止の知らせの再送が同じ結果でやり直す（実行の移し替えは条件つきで、2 度目は何もしない）
    if (run.currentJobId === job.jobId) {
      if (run.cancelRequested) {
        const lw = await this.loopWriteFor(run, job.jobId, { runState: "cancelled", failureReason: null, finishReason });
        if (await table.finishRun(run.sprintRunId, "cancelled", null, now, { finishReason, loop: lw?.write, requests })) await this.afterRunMoved(run, lw?.write, true);
      } else if (isReleaseRun(run)) {
        // 配布の実行は、配布済みで done、承認ゲートか判断で承認待ち、それ以外は止まった配布として承認待ち（I65・決定 11）
        if (releaseEvents?.settled?.result === "released") {
          await table.finishRun(run.sprintRunId, "done", null, now);
        } else if (result === "gate") {
          await table.awaitApproval(run.sprintRunId, job.jobId, now);
        } else {
          await table.awaitApproval(run.sprintRunId, job.jobId, now, { awaitingReason: "release_stalled" });
        }
      } else if (result === "gate") {
        const lw = await this.loopWriteFor(run, job.jobId, { runState: "awaiting_approval", failureReason: null, finishReason });
        const waitsRelease = rawReason === "release_requested" && !run.loopId && lastRequest !== null;
        await table.awaitApproval(run.sprintRunId, job.jobId, now, {
          loop: lw?.write,
          requests,
          ...(waitsRelease ? { awaitingReason: "release_requested" as const, releaseRequestId: lastRequest } : {}),
        });
      } else if (result === "stopped" && finishReason === "usage_limit" && run.executionMode === "local") {
        // local の利用上限はリセット時刻の後に同じ周回を続ける（I54。ほかの形態は failed として扱う）
        const resumeAt = this.resumeAtOf(finished);
        const lw = await this.loopWriteFor(run, job.jobId, { runState: "awaiting_reset", failureReason: null, finishReason });
        await table.awaitReset(run.sprintRunId, job.jobId, resumeAt, now, { finishReason, loop: lw?.write });
      } else {
        const { state, failure } = runEndOf(result, finishReason, job, finished, reason);
        const lw = await this.loopWriteFor(run, job.jobId, { runState: state, failureReason: failure, finishReason });
        if (await table.finishRun(run.sprintRunId, state, failure, now, { finishReason, loop: lw?.write, requests })) await this.afterRunMoved(run, lw?.write, true);
      }
    }
    // 依頼を受けたか配布の実行が動いたら、配布のまとめを進める。落ちてよい（見回りが続ける）
    if (this.organizer && (isReleaseRun(run) || requests.length > 0)) {
      await this.organizer.tick(run.accountId, run.repositoryId).catch((err: Error) =>
        this.log({ event: "organizer_tick_failed", sprint_run_id: run.sprintRunId, error: err.name }),
      );
    }
    const updated = await table.updateJob(job.jobId, { state: result, result, finishedAt: now }, ["pending", "running"]);
    if (!updated) return;
    if (job.tokenSecretArn) {
      await platform.deleteJobToken(job.tokenSecretArn).catch((err: Error) =>
        this.log({ event: "token_delete_failed", job_id: job.jobId, error: err.name }),
      );
    }
  }

  // job.finished を読む。無ければ control で failed を書き、書けなければ（先に書かれていれば）読み直す
  private async ensureFinished(run: SprintRun, job: RunJob, reason: FailureReason): Promise<JobFinishedEvent> {
    const { reader } = this.deps;
    const found = await reader.findJobFinished(run.sprintRunId, job.jobId);
    if (found) return found;
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
    return written ? event : ((await reader.findJobFinished(run.sprintRunId, job.jobId)) ?? event);
  }

  // local の利用上限の再開の時刻。resets_at を読めなければ usageLimitRetrySeconds 後
  private resumeAtOf(finished: JobFinishedEvent): string {
    const resetsAt = finished.payload.resets_at ?? null;
    const retry = (this.deps.config.usageLimitRetrySeconds ?? DEFAULT_USAGE_LIMIT_RETRY_SECONDS) * 1000;
    return resetsAt && !Number.isNaN(Date.parse(resetsAt)) ? new Date(resetsAt).toISOString() : new Date(this.now().getTime() + retry).toISOString();
  }

  // 見回り（5 分ごと）。打ち切りの時間を過ぎたジョブを止め、止まったタスクと起動しないジョブを終わらせる（I8）。
  // リセット時刻を過ぎた実行を再開し、次の周回を待つループを起こす（#2347）
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
    for (const run of await table.listResetRuns(this.iso())) {
      await this.resumeAfterReset(run).catch((err: Error) => this.log({ event: "reset_resume_failed", sprint_run_id: run.sprintRunId, error: err.name }));
    }
    await this.hooks?.sweepLoops().catch((err: Error) => this.log({ event: "loop_sweep_failed", error: err.name }));
    await this.organizer?.sweep().catch((err: Error) => this.log({ event: "organizer_sweep_failed", error: err.name }));
  }

  // リセット待ちの実行に、承認の依頼の識別子の無い resume のジョブを作る（決定 7）。時刻の前は何もしない
  async resumeAfterReset(run: SprintRun): Promise<boolean> {
    if (run.state !== "awaiting_reset" || !run.resumeAt || Date.parse(run.resumeAt) > this.now().getTime()) return false;
    // 人の操作を経ずに起こすため、始めた利用者がまだ席を持ち、形態が local のままかを照らす（ループの周回の開始と同じ）
    if (this.deps.resolveContext) {
      const fresh = await this.deps.resolveContext(run.accountId, run.createdBy);
      if (!fresh || fresh.executionMode !== run.executionMode) {
        const lw = await this.loopWriteFor(run, null, { runState: "failed", failureReason: "seat_missing", finishReason: run.finishReason ?? null });
        if (await this.deps.table.finishRun(run.sprintRunId, "failed", "seat_missing", this.iso(), { loop: lw?.write })) await this.afterRunMoved(run, lw?.write, true);
        return false;
      }
    }
    const job = this.newJob(run, run.jobCount + 1, "resume", null);
    job.loopInput = await this.resumeLoopInput(run);
    if (!(await this.deps.table.addResumeJob(job, this.iso(), { from: "awaiting_reset" }))) return false;
    await this.launch(run, job);
    return true;
  }

  private async resumeLoopInput(run: SprintRun): Promise<LoopJobInput | null> {
    if (!run.loopId) return null;
    const loop = await this.deps.table.getLoop(run.accountId, run.loopId);
    if (!loop) return null;
    const exclude = (await this.deps.table.listIssueShares(run.accountId, run.repositoryId)).map((s) => s.number);
    return loopInput(loop, exclude);
  }

  // 再開の要求（#2173）。承認待ちで動くジョブが無いときだけ resume のジョブを作り起動する（I8・I9）。
  // 配布の実行は、配布のまとめが答えから再開の手を決める。差し戻しはジョブを起こさず jobId が null（#2439）
  async requestResume(input: { sprintRunId: string; approvalRequestId: string }): Promise<{ jobId: string | null }> {
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
    // 配布の依頼の答えを待つ実行と止まった配布は、承認の依頼の答えでは再開しない（配布のまとめが再開する）
    if (run.awaitingReason) throw new ResumeRejectedError("not_awaiting_approval");
    const seq = run.jobCount + 1;
    let releaseStep: ReleaseStep | null = null;
    if (isReleaseRun(run)) {
      if (!this.organizer) throw new ServiceUnavailableError("sprint_run_unavailable");
      releaseStep = await this.organizer.decide(run, input.approvalRequestId);
      if (releaseStep === null) return { jobId: null };
    }
    const job = this.newJob(run, seq, releaseStep ? "release" : "resume", input.approvalRequestId);
    if (releaseStep) job.releaseStep = releaseStep;
    job.loopInput = await this.resumeLoopInput(run);
    // ループは答えで running へ戻る（同じトランザクション）
    const loop = run.loopId ? await table.getLoop(run.accountId, run.loopId) : null;
    const loopWrite = loop && loop.state === "awaiting_approval" ? loopWriteOnResume(loop, run, this.iso()) : undefined;
    if (!(await table.addResumeJob(job, this.iso(), { loop: loopWrite }))) {
      const now = await table.getRun(run.sprintRunId);
      throw new ResumeRejectedError(now?.state === "awaiting_approval" || now?.state === "starting" ? "sprint_run_busy" : "not_awaiting_approval");
    }
    await this.launch(run, job);
    return { jobId: job.jobId };
  }

  // 手動の実行の配布の答えの再開（#2439）。実行がこの依頼を待つときだけ、条件つきの書き込みで resume のジョブを作る。
  // 待っていなければ not_waiting（取り消し・終わり・別の理由や別の依頼での承認待ち）、ジョブが動いていれば busy
  async resumeForRelease(sprintRunId: string, requestId: string): Promise<"resumed" | "busy" | "not_waiting"> {
    const { table } = this.deps;
    const run = await table.getRun(sprintRunId);
    const waits = (r: SprintRun | null) => r !== null && r.state === "awaiting_approval" && r.awaitingReason === "release_requested" && r.releaseRequestId === requestId;
    if (!waits(run)) return run && !isTerminalRun(run.state) && run.currentJobId !== null ? "busy" : "not_waiting";
    const job = this.newJob(run!, run!.jobCount + 1, "resume", null);
    job.releaseRequestId = requestId;
    if (!(await table.addResumeJob(job, this.iso(), { expectAwaiting: { reason: "release_requested", releaseRequestId: requestId } }))) {
      return waits(await table.getRun(sprintRunId)) ? "busy" : "not_waiting";
    }
    await this.launch(run!, job);
    return "resumed";
  }

  // 止まった配布の実行を「続きから流す」（#2439 の F7）。止まった配布の承認待ちのときだけ書く。配布の実行は作り直さない（I57）
  async resumeRelease(run: SprintRun, step: ReleaseStep): Promise<boolean> {
    const job = this.newJob(run, run.jobCount + 1, "release", null);
    job.releaseStep = step;
    if (!(await this.deps.table.addResumeJob(job, this.iso(), { expectAwaiting: { reason: "release_stalled" } }))) return false;
    await this.launch(run, job);
    return true;
  }

  // 取り消し。動くジョブがあれば止め、停止の後に cancelled へ移す。無ければその場で cancelled にする
  async cancelSprintRun(ctx: AccountContext, sprintRunId: string): Promise<{ state: "cancelled" | "running" }> {
    const { table, platform } = this.deps;
    const run = await table.getRun(sprintRunId);
    if (!run || run.accountId !== ctx.accountId) throw new NotFoundError("sprint_run_not_found", "スプリントの実行が見つかりません");
    if (isTerminalRun(run.state)) throw new ConflictError("already_finished", "このスプリントの実行は終わっています");
    if (run.currentJobId === null) {
      const lw = await this.loopWriteFor(run, null, { runState: "cancelled", failureReason: null, finishReason: null });
      if (await table.finishRun(run.sprintRunId, "cancelled", null, this.iso(), { loop: lw?.write })) {
        await this.afterRunMoved(run, lw?.write, true);
        return { state: "cancelled" };
      }
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

  // 一覧に載せる実行（絞り込みと並び）。行の形は一覧の経路（routes/sprint-views.ts）が作る
  async listSprintRuns(ctx: AccountContext, repositoryId: number | null): Promise<SprintRun[]> {
    const runs = await this.deps.table.listRuns(ctx.accountId, repositoryId === null ? 50 : 1000);
    return runs.filter((r) => repositoryId === null || r.repositoryId === repositoryId).slice(0, 50);
  }

  // 形態 local の渡す口（#2189）。最も古い pending の local のジョブを running にし、指示と token を 1 回だけ返す。
  // 手元のマシンの申告があれば、そのマシンの枠が同時のジョブの数に達している間は渡さない（I50。申告が無ければ 1）
  async claimLocalRunJob(accountId: string, runner?: LocalRunner): Promise<{ instruction: RunJobInstruction; token: string } | null> {
    const { table } = this.deps;
    for (const job of await table.listActiveJobs()) {
      if (job.runnerKind !== "local" || job.state !== "pending" || job.accountId !== accountId) continue;
      const run = await table.getRun(job.sprintRunId);
      if (!run || run.cancelRequested) continue;
      if (runner) {
        const now = this.iso();
        if (!(await table.claimLocalJob(job.jobId, runner.runnerId, runner.maxJobs ?? 1, now))) {
          const current = await table.getJob(job.jobId);
          // 枠で断られた（ジョブは pending のまま）なら、ほかのジョブも渡せない
          if (current?.state === "pending") return null;
          continue;
        }
        await table.markRunRunning(job.sprintRunId, now);
      } else if (!(await this.markRunning(job))) {
        continue;
      }
      const token = await this.issueToken(run, job);
      // local の token は置き場に書かない。指示の形を保つため、置き場の項目には local を表す ARN を置く
      const instruction = this.buildInstruction(run, job, `arn:aws:secretsmanager:local:000000000000:secret:ndf-factory/run-jobs/${job.jobId}`);
      return { instruction, token };
    }
    return null;
  }

  // 手元のマシンのジョブの終わりか心拍の切れ（#2189）。停止の知らせと同じ処理
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

// ループの実行のジョブの指示へ載せる値（予算・答えは周回を起こす時点の値）。答えは今の人の判断待ちに残る番号だけ
export function loopInput(loop: DevLoop, exclude: number[]): LoopJobInput {
  const open = new Set(loop.pending.map((p) => p.number));
  return { name: loop.name, milestones: loop.milestones, budget: loop.budget, exclude, answers: loop.answers.filter((a) => open.has(a.number)) };
}
