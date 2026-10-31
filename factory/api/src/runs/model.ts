// 集約「スプリントの実行」「実行の枠」と、同じ表に置くリポジトリの登録・持ち込んだキーの行（#2186 の設計の「データ構造」）
import type { ExecutionMode } from "../accounts/model.ts";

export const RUN_STATES = ["starting", "running", "awaiting_approval", "done", "stopped", "failed", "cancelled"] as const;
export type RunState = (typeof RUN_STATES)[number];
export const TERMINAL_RUN_STATES: ReadonlySet<RunState> = new Set(["done", "stopped", "failed", "cancelled"]);
export const isTerminalRun = (state: RunState) => TERMINAL_RUN_STATES.has(state);

export const JOB_STATES = ["pending", "running", "gate", "stopped", "done", "failed"] as const;
export type JobState = (typeof JOB_STATES)[number];
export const isTerminalJob = (state: JobState) => state !== "pending" && state !== "running";

export type FailureReason = "launch_failed" | "timeout" | "task_stopped" | "job_failed" | "credential_unavailable";

export interface Registration {
  accountId: string;
  repositoryId: number;
  fullName: string;
  installationId: number;
  registeredBy: string;
  registeredAt: string;
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
}

export interface RunJob {
  jobId: string;
  sprintRunId: string;
  accountId: string;
  seq: number;
  start: "new" | "resume";
  approvalRequestId: string | null;
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

// 実行の枠（I34）。アカウント全体とリポジトリごとの、終わりの状態でないスプリントの実行の数
export interface Slots {
  account: number;
  repository: number;
}
