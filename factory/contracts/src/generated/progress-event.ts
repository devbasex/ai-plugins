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
  | CiUpdatedEvent
  | DecisionDelegatedEvent
  | IssueRequestedEvent
  | IssueRequestResolvedEvent
  | SectionStartedEvent
  | LoopLapClosedEvent
  | LoopStoppedEvent
  | ReleaseRequestedEvent
  | ReleasePresentedEvent
  | ReleaseProgressEvent
  | ReleaseSettledEvent
  | ReleaseAnsweredEvent;
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
/**
 * NDF の開発ループの名前
 */
export type LoopName = string;
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
export type DecisionDelegatedEvent = EventCommon & {
  type: "decision.delegated";
  payload: DecisionDelegatedPayload;
};
export type IssueRequestedEvent = EventCommon & {
  type: "issue.requested";
  payload: IssueRequestedPayload;
};
export type IssueRequestResolvedEvent = EventCommon & {
  type: "issue.request_resolved";
  payload: IssueRequestResolvedPayload;
};
export type SectionStartedEvent = EventCommon & {
  type: "section.started";
  payload: SectionStartedPayload;
};
export type LoopLapClosedEvent = EventCommon & {
  type: "loop.lap_closed";
  payload: LoopLapClosedPayload;
};
export type LoopStoppedEvent = EventCommon & {
  type: "loop.stopped";
  payload: LoopStoppedPayload;
};
/**
 * NDF の開発ループの止まる条件
 */
export type LoopStopCondition =
  | "user_stop"
  | "mvv_changed"
  | "same_failure"
  | "budget_laps"
  | "budget_usd"
  | "budget_hours"
  | "no_issue"
  | "outside_mvv"
  | "gate2_returned"
  | "refused";
export type ReleaseRequestedEvent = EventCommon & {
  type: "release.requested";
  payload: ReleaseRequestedPayload;
};
/**
 * NDF が作る配布の依頼の id（<レーンの名前>-r<乱数>）
 */
export type ReleaseRequestId = string;
/**
 * 並列のレーンの名前（Factory では loop-<ループの ID> / run-<実行の ID>。#2439）
 */
export type ReleaseLaneName = string;
export type NumberList = number[];
export type ReleasePresentedEvent = EventCommon & {
  type: "release.presented";
  payload: ReleasePresentedPayload;
};
export type ReleaseCommit = string;
export type ReleaseVersion = string;
export type ReleaseProgressEvent = EventCommon & {
  type: "release.progress";
  payload: ReleaseProgressPayload;
};
export type ReleaseSettledEvent = EventCommon & {
  type: "release.settled";
  payload: ReleaseSettledPayload;
};
export type ReleaseAnsweredEvent = EventCommon & {
  type: "release.answered";
  payload: ReleaseAnsweredPayload;
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
  start: "new" | "resume" | "loop" | "release";
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
  loop?: LoopRef;
  /**
   * 答えの選択肢の制限。配布の実行が main へのマージの後に判断で止まったときは continue だけを載せる（#2439 の I63）
   *
   * @minItems 1
   */
  choices?: ["continue" | "reject", ...("continue" | "reject")[]];
}
/**
 * 開発ループの実行のとき、ループの名前と周回の番号（#2347）
 */
export interface LoopRef {
  name: LoopName;
  lap: number;
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
  /**
   * stopped の理由。section_limit は区間の上限、usage_limit は local の利用上限。release_requested は gate の理由で、手動の実行が配布の依頼を出して答えを待つ（#2439）
   */
  reason?: "section_limit" | "usage_limit" | "release_requested";
  /**
   * usage_limit のときだけ。利用上限のリセット時刻（読めなければ null）
   */
  resets_at?: string | null;
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
/**
 * 承認ゲートの外の問い合わせを hook が止めた後に、AI が自分で下した判断（#2206）
 */
export interface DecisionDelegatedPayload {
  question: string;
  answer: string;
  reason: string;
  /**
   * 止めたときの metadata.source。無ければ null
   */
  category: string | null;
}
/**
 * 実行のリポジトリと別のリポジトリへの起票の依頼（#2216）。起票はサービスが行い、ランナーは送るだけ
 */
export interface IssueRequestedPayload {
  /**
   * GitHub のリポジトリ（owner/name）
   */
  repo: string;
  title: string;
  body: string;
}
/**
 * 起票の依頼 1 件ごとの結果（#2216）。filed は起票した Issue の参照を、drafted は下書きへ回した理由を持つ
 */
export interface IssueRequestResolvedPayload {
  /**
   * 依頼の冪等の鍵（<job_id>:<seq>）
   */
  request_key: string;
  outcome: "filed" | "drafted";
  issue?: IssueRef;
  reason?:
    | "not_registered"
    | "not_installed"
    | "unsupported_tracker"
    | "visibility"
    | "secret_suspected"
    | "limit_exceeded"
    | "filing_failed";
  /**
   * 同じ中身の最初の依頼の鍵。最初の依頼なら null
   */
  duplicate_of: string | null;
}
/**
 * サービスが起票した Issue の参照
 */
export interface IssueRef {
  repo: RepoFullName;
  number: number;
  url: string;
}
/**
 * 1 つの実行ジョブの中でランナーが claude -p を起動した（区間。#2347）
 */
export interface SectionStartedPayload {
  /**
   * 1 から数える
   */
  section: number;
  /**
   * 起動の指示。2,000 字で切る
   */
  command: string;
}
/**
 * NDF の開発ループが締めた周回（.ndf/loop/<名>/loop.json の翻訳）
 */
export interface LoopLapClosedPayload {
  loop: LoopName;
  lap: number;
  issues: number[];
  result: "完了" | "失敗" | "人へ戻した";
  /**
   * 開発版と本番の版。無ければ null
   */
  version: string | null;
  cost_usd: number;
  seconds: number;
  pending: LoopPending[];
}
/**
 * 人の判断待ちの課題（承認ゲート 1 で MVV 判定が「従う」以外）
 */
export interface LoopPending {
  number: number;
  reason: string;
  lap: number;
}
/**
 * NDF の開発ループが止まる条件に当たった（loop.json の stop の翻訳）
 */
export interface LoopStoppedPayload {
  loop: LoopName;
  condition: LoopStopCondition;
  /**
   * stop の残りの値の JSON
   */
  detail: string;
  pending: LoopPending[];
}
/**
 * 実行が本番の配布の代わりに出した配布の依頼（#2439）。NDF の置き場の requests/ の写し
 */
export interface ReleaseRequestedPayload {
  request_id: ReleaseRequestId;
  lane: ReleaseLaneName;
  prs: NumberList;
  issues: NumberList;
  ndf_version: string;
  requested_at: string;
}
/**
 * 配布の実行が承認資料をそろえた提示（#2439）。auto は MVV 判定が承認ゲート 2 を通したとき真
 */
export interface ReleasePresentedPayload {
  seq: number;
  /**
   * 提示の番号。出し直しは 2
   */
  k: 1 | 2;
  sha: ReleaseCommit;
  version: ReleaseVersion;
  prs: NumberList;
  lanes: ReleasePresentedLane[];
  added_prs: NumberList;
  auto: boolean;
}
export interface ReleasePresentedLane {
  lane: ReleaseLaneName;
  /**
   * そのレーンが配布の依頼を出したか
   */
  requested: boolean;
}
/**
 * 配布の実行の本番の配布が届いた段（#2439）
 */
export interface ReleaseProgressPayload {
  seq: number;
  stage: "release_branch" | "develop_merged" | "prod_pr_opened" | "main_merged" | "tagged";
}
/**
 * 配布の実行の結果（#2439）
 */
export interface ReleaseSettledPayload {
  seq: number;
  result: "released" | "failed";
  version?: ReleaseVersion;
  prod_pr_url?: string;
  sha?: ReleaseCommit;
  prs: NumberList;
  lanes: ReleaseLaneName[];
  reason?: string;
}
/**
 * Factory のオーガナイザーが範囲の実行へ返した配布の答え（#2439）
 */
export interface ReleaseAnsweredPayload {
  seq: number;
  request_id?: ReleaseRequestId;
  result: "released" | "rejected";
  version?: ReleaseVersion;
  prod_pr_url?: string;
}
