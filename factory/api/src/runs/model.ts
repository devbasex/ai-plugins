// 集約「スプリントの実行」「実行の枠」「開発ループ」と、同じ表に置くリポジトリの登録・持ち込んだキーの行（#2186・#2347 の設計の「データ構造」）
import { createHash } from "node:crypto";
import type { AccountContext, ExecutionMode } from "../accounts/model.ts";

// awaiting_reset は local の利用上限のリセット時刻を待つ（終わりの状態でない。枠を数え、今のジョブは空。#2347 の決定 7）
export const RUN_STATES = ["starting", "running", "awaiting_approval", "awaiting_reset", "done", "stopped", "failed", "cancelled"] as const;
export type RunState = (typeof RUN_STATES)[number];
export const TERMINAL_RUN_STATES: ReadonlySet<RunState> = new Set(["done", "stopped", "failed", "cancelled"]);
export const isTerminalRun = (state: RunState) => TERMINAL_RUN_STATES.has(state);

export const JOB_STATES = ["pending", "running", "gate", "stopped", "done", "failed"] as const;
export type JobState = (typeof JOB_STATES)[number];
export const isTerminalJob = (state: JobState) => state !== "pending" && state !== "running";

// seat_missing は、自動の再開の時点で実行を始めた利用者が席を持っていなかった（#2347）
export type FailureReason = "launch_failed" | "timeout" | "task_stopped" | "job_failed" | "credential_unavailable" | "seat_missing";

export interface Registration {
  accountId: string;
  repositoryId: number;
  fullName: string;
  installationId: number;
  registeredBy: string;
  registeredAt: string;
  // 同時の実行の数の宣言。空は「宣言しない（プランの値）」（I49）
  maxConcurrentRuns?: number | null;
}

export type CredentialKind = "anthropic_api_key" | "aws_role";

export interface CredentialRow {
  accountId: string;
  kind: CredentialKind;
  secretArn: string;
  updatedAt: string;
}

export interface SprintRun {
  sprintRunId: string;
  accountId: string;
  repositoryId: number;
  repo: string;
  state: RunState;
  issues: number[];
  ndfVersion: string;
  executionMode: ExecutionMode;
  modelCredentialSecretArn: string | null;
  currentJobId: string | null;
  // 作った実行ジョブの数。次のジョブの seq になる
  jobCount: number;
  cancelRequested: boolean;
  failureReason: FailureReason | null;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
  // 属する開発ループと周回の番号。空は手動の実行（#2347）
  loopId?: string | null;
  lap?: number | null;
  // 古い版として legacyCount に数えた実行（I48）。空は #2347 より前に作られ、まだ埋め戻していない
  legacy?: boolean;
  // job.finished の reason（section_limit / usage_limit）
  finishReason?: FinishReason | null;
  // awaiting_reset の間だけ。リセット時刻
  resumeAt?: string | null;
}

export type FinishReason = "section_limit" | "usage_limit";

// ループの実行のジョブの指示へ載せる値。ジョブを作る時点で写す（指示の組み立てを表の読みから切り離す）
export interface LoopJobInput {
  name: string;
  milestones: string[];
  budget: LoopBudget;
  exclude: number[];
  answers: LoopAnswer[];
}

export interface RunJob {
  jobId: string;
  sprintRunId: string;
  accountId: string;
  seq: number;
  start: "new" | "resume" | "loop";
  approvalRequestId: string | null;
  // ループの実行だけ（#2347）
  loopInput?: LoopJobInput | null;
  // local で受け取った手元のランナー。手元のランナーの枠を減らす先（I50）
  runnerId?: string | null;
  state: JobState;
  runnerKind: "ecs" | "local";
  taskArn: string | null;
  tokenSecretArn: string | null;
  // 見回りが止めた理由。停止の知らせで failed にするときの失敗の理由になる
  stopReason: FailureReason | null;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  result: "gate" | "stopped" | "done" | "failed" | null;
}

// 実行の枠（I34）。アカウント全体とリポジトリごとの、終わりの状態でないスプリントの実行の数と、
// リポジトリの古い版の実行の数（I48。無いのは 0）
export interface Slots {
  account: number;
  repository: number;
  legacy?: number;
}

// ---- 開発ループ（#2347） ----

export const LOOP_STATES = ["running", "between_laps", "awaiting_approval", "stopped", "cancelled"] as const;
export type LoopState = (typeof LOOP_STATES)[number];
export const isTerminalLoop = (state: LoopState) => state === "stopped" || state === "cancelled";

// between_laps の理由。空は「起こす途中」
export type WaitReason = "slots" | "legacy_version" | "user_start";

export interface LoopBudget {
  laps: number | null;
  usd: number | null;
  hours: number | null;
}

export interface LoopPending {
  number: number;
  reason: string;
  lap: number;
}

export interface LoopAnswer {
  number: number;
  decision: "approved" | "rejected";
  pr: number | null;
}

// 周回を起こすときの判定に使うアカウントの文脈の写し（見回りは要求を持たないため。利用者の操作のたびに写し直す）
export interface LoopContext {
  userId: string;
  plan: AccountContext["plan"];
  seatType: AccountContext["seatType"];
  executionMode: ExecutionMode;
  kind: AccountContext["kind"];
  role: AccountContext["role"];
}

export interface DevLoop {
  loopId: string;
  accountId: string;
  repositoryId: number;
  name: string;
  milestones: string[];
  budget: LoopBudget;
  state: LoopState;
  waitReason: WaitReason | null;
  currentRunId: string | null;
  lapRuns: number;
  lapsClosed: number;
  closedCostUsd: number;
  closedSeconds: number;
  stopCondition: string | null;
  stopDetail: string | null;
  pending: LoopPending[];
  answers: LoopAnswer[];
  cancelRequested: boolean;
  context: LoopContext;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
  lastRunAt: string | null;
}

// ループの行への書き込み（周回の開始・実行の終わりへの移り・取り消しと同じトランザクションで書く。決定 2）
export interface LoopWrite {
  accountId: string;
  loopId: string;
  set: Partial<Omit<DevLoop, "loopId" | "accountId">>;
  // 今の値がこれと同じときだけ書く
  expect: { currentRunId?: string | null; lapRuns?: number; states?: LoopState[] };
  // 持ち分を消すマイルストーン（ループが終わりの状態へ移るとき）
  releaseMilestones?: { repositoryId: number; titles: string[] };
}

// 開始の書き込みの条件（I45〜I48）
export interface CreateRunGuards {
  // 書く課題の持ち分（手動の開始）
  issues: number[];
  // 持ち分が無いことを照合するマイルストーンの題（手動の開始。題ごとに 1 件）
  milestoneChecks: string[];
  // 古い版として legacyCount を 1 増やす。偽ならリポジトリの legacyCount が 0 のときだけ書く
  legacy: boolean;
  // ループの周回の開始。ループの行を条件つきで書く
  loop?: LoopWrite;
}

export type CreateRunResult = "created" | "slots" | "issue_in_use" | "issue_in_loop" | "legacy" | "loop_changed";

// マイルストーンの持ち分の行の鍵に使う、題の sha256 の先頭 32 字
export const milestoneHash = (title: string) => createHash("sha256").update(title, "utf8").digest("hex").slice(0, 32);
