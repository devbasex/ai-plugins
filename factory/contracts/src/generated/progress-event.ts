// 生成したファイル。手で直さない。定義（schemas/）を直して `npm run generate -w contracts` で作り直す

/**
 * 進捗の事象 1 件。確定仕様 docs/specifications/ndf-factory-service-boundary.md の「進捗の正本」と「進捗の事象の種類と受け口」。order_key と pr_ref は受け口が作るため含めない
 */
export type ProgressEvent =
  | JobStartedEvent
  | PhaseChangedEvent
  | StepRecordedEvent
  | AttentionEvent
  | GateReachedEvent
  | GateAnsweredEvent
  | UsageRecordedEvent
  | JobFinishedEvent
  | PrUpdatedEvent
  | CiUpdatedEvent;
export type JobStartedEvent = EventCommon & {
  type: "job.started";
  payload: JobStartedPayload;
};
/**
 * スプリントの実行・実行ジョブの識別子。order_key の区切りの # と冪等の鍵の区切りの : を含めない
 */
export type Identifier = string;
export type EventSource = "runner" | "github" | "api" | "control";
export type PhaseChangedEvent = EventCommon & {
  type: "phase.changed";
  payload: PhaseChangedPayload;
};
export type StepRecordedEvent = EventCommon & {
  type: "step.recorded";
  payload: StepRecordedPayload;
};
/**
 * GitHub のリポジトリ（owner/name）
 */
export type RepoFullName = string;
export type PullRequestNumber = number;
export type AttentionEvent = EventCommon & {
  type: "attention";
  payload: AttentionPayload;
};
export type GateReachedEvent = EventCommon & {
  type: "gate.reached";
  payload: GateReachedPayload;
};
/**
 * ゲート 1・ゲート 2・判断
 */
export type GateKind = "gate1" | "gate2" | "decision";
export type GateAnsweredEvent = EventCommon & {
  type: "gate.answered";
  payload: GateAnsweredPayload;
};
export type UsageRecordedEvent = EventCommon & {
  type: "usage.recorded";
  payload: UsageRecordedPayload;
};
export type JobFinishedEvent = EventCommon & {
  type: "job.finished";
  payload: JobFinishedPayload;
};
export type PrUpdatedEvent = EventCommon & {
  type: "pr.updated";
  payload: PrUpdatedPayload;
};
export type CiUpdatedEvent = EventCommon & {
  type: "ci.updated";
  payload: CiUpdatedPayload;
};

/**
 * どの種類にも共通の列
 */
export interface EventCommon {
  sprint_run_id: Identifier;
  /**
   * 重複の判定の鍵。出所ごとの形は受け口の照合が見る
   */
  idempotency_key: string;
  type: string;
  /**
   * 事象が起きた時刻（ISO 8601、UTC）。送り直しでも変えない
   */
  at: string;
  source: EventSource;
  /**
   * null は実行ジョブの外で起きた事象（github・api）
   */
  job_id: string | null;
  schema_version: 1;
  payload: {};
}
export interface JobStartedPayload {
  ndf_version: string;
  start: "new" | "resume";
}
export interface PhaseChangedPayload {
  /**
   * フェーズの名前。集合は #2172 が決めるまで列挙にしない
   */
  phase: string;
  issues: number[];
}
export interface StepRecordedPayload {
  step: string;
  exit_code: number;
  seconds: number;
  summary: string;
  pull_request?: PullRequestRef;
}
/**
 * このステップが作った・更新した Pull Request
 */
export interface PullRequestRef {
  repo: RepoFullName;
  number: PullRequestNumber;
}
export interface AttentionPayload {
  reason: string;
  summary: string;
}
export interface GateReachedPayload {
  kind: GateKind;
  pr_url: string | null;
  /**
   * 承認資料の本文
   */
  material: string;
}
/**
 * 本文は持たない。decision は kind が decision のときは持たず、ほかでは必須
 */
export interface GateAnsweredPayload {
  approval_request_id: string;
  kind: GateKind;
  decision?: "approved" | "rejected";
  answered_by_account_id: string;
}
export interface UsageRecordedPayload {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  step: string;
}
export interface JobFinishedPayload {
  result: "gate" | "stopped" | "done" | "failed";
  summary: string;
}
export interface PrUpdatedPayload {
  repo: RepoFullName;
  number: PullRequestNumber;
  state: "open" | "closed" | "merged";
}
export interface CiUpdatedPayload {
  repo: RepoFullName;
  pr_number: PullRequestNumber;
  name: string;
  /**
   * null はまだ終わっていない
   */
  conclusion: string | null;
}
