// 実行の表（RUNS_TABLE_NAME）の口。条件つきの書き込みを操作の単位で持ち、同時の 2 本でも不変条件が崩れない形にする
import type { OrganizerRoot, OrganizerWrite, OrgLog, OrgRequest, ReleaseAnswer, ReleaseBatch } from "../organizer/model.ts";
import type {
  AwaitingReason,
  CreateRunGuards,
  CreateRunResult,
  CredentialRow,
  DevLoop,
  FailureReason,
  FinishReason,
  LoopWrite,
  Registration,
  RunJob,
  RunState,
  Slots,
  SprintRun,
} from "./model.ts";
import type { IdempotencyClaim, IdempotencyRef } from "../idempotency.ts";

// 終わりへの移り・承認待ち・リセット待ちに添える書き込み（決定 2。ループは実行と同じトランザクションで書く）
export interface RunTransitionExtras {
  finishReason?: FinishReason | null;
  loop?: LoopWrite;
  // 受けた配布の依頼の行（#2439 の I59）。無い行だけを渡す。実行の移りと同じトランザクションで書く
  requests?: OrgRequest[];
  // 承認待ちの理由と、手動の実行が待つ配布の依頼（I65。awaitApproval だけが読む）
  awaitingReason?: AwaitingReason | null;
  releaseRequestId?: string | null;
  // 取り消しの印を外す（main へマージした後に取り消しを受けた配布の実行を、止まった配布として残すとき。awaitApproval だけが読む）
  clearCancel?: boolean;
}

// 再開のジョブを作る条件（#2439）。expectAwaiting を省くと、承認待ちの理由が空のときだけ書く
export interface ResumeOptions {
  from?: "awaiting_approval" | "awaiting_reset";
  loop?: LoopWrite;
  expectAwaiting?: { reason: AwaitingReason; releaseRequestId?: string };
  // まとめた配布の根と配布の書き込み（止まった配布を続けるとき。状態の戻しとジョブの作成を同じトランザクションで書く）
  organizer?: OrganizerWrite;
}

// まとめた配布と配布の実行を 1 回で書く（I57）。legacySeq はリポジトリの枠の legacySeq を照らした値（無ければ null。I62）
export interface ComposeRelease {
  write: OrganizerWrite & { batch: ReleaseBatch; log: OrgLog };
  run: SprintRun;
  job: RunJob;
  legacySeq: number | null;
}

export interface RunsTable {
  // リポジトリの登録（I32・I36）
  getRegistration(accountId: string, repositoryId: number): Promise<Registration | null>;
  listRegistrations(accountId: string): Promise<Registration[]>;
  // 同じアカウントに同じ ID があれば false（I32）
  putRegistration(registration: Registration): Promise<boolean>;
  setRegistrationName(accountId: string, repositoryId: number, fullName: string): Promise<void>;
  // 同時の実行の数の宣言（I49）。登録が無ければ false
  setMaxConcurrentRuns(accountId: string, repositoryId: number, value: number | null): Promise<boolean>;
  // リポジトリの枠が 0 でなければ in_use で書かない（I36）
  deleteRegistration(accountId: string, repositoryId: number): Promise<"deleted" | "not_found" | "in_use">;

  // 持ち込んだキー（値は持たない。I38）
  getCredential(accountId: string): Promise<CredentialRow | null>;
  putCredential(row: CredentialRow): Promise<void>;
  deleteCredential(accountId: string): Promise<void>;

  // 実行の枠（I34）と古い版の数（I48）
  getSlots(accountId: string, repositoryId: number): Promise<Slots>;

  // 実行・最初のジョブ・枠を 1 回で書く。枠が expected と違えば slots（決定 5）。guards があれば課題の持ち分・
  // マイルストーンの照合・古い版の数・ループの行も同じトランザクションで書き、落ちた条件を返す（#2347 の決定 2・11・12）
  createRun(run: SprintRun, job: RunJob, expected: Slots, guards?: CreateRunGuards): Promise<CreateRunResult>;
  getRun(sprintRunId: string): Promise<SprintRun | null>;
  // アカウントの実行の新しい順
  listRuns(accountId: string, limit: number): Promise<SprintRun[]>;
  setCancelRequested(sprintRunId: string): Promise<void>;
  // 終わりの状態でない実行を running にする（starting のときだけ）
  markRunRunning(sprintRunId: string, now: string): Promise<void>;
  // 承認待ち（from が awaiting_reset ならリセット待ち）の実行に resume のジョブを足す。動くジョブが無いときだけ書く（I9）
  addResumeJob(job: RunJob, now: string, opts?: ResumeOptions): Promise<boolean>;
  // 動くジョブを外し、実行を承認待ちへ移す（終わりの状態でないときだけ）
  awaitApproval(sprintRunId: string, jobId: string, now: string, extras?: RunTransitionExtras): Promise<boolean>;
  // 動くジョブを外し、実行をリセット待ちへ移す（local の利用上限。#2347 の決定 7）
  awaitReset(sprintRunId: string, jobId: string, resumeAt: string, now: string, extras?: RunTransitionExtras): Promise<boolean>;
  // リセット時刻が before 以前のリセット待ちの実行（見回りが引く）
  listResetRuns(before: string): Promise<SprintRun[]>;
  // 終わりの状態へ移し、2 つの枠を 1 ずつ減らす。課題の持ち分を消し、古い版の実行なら legacyCount も減らす。
  // 配布の実行は枠を持たないため減らさない（I64）。既に終わりの状態なら false（I34・I47・I48）
  finishRun(sprintRunId: string, state: RunState, reason: FailureReason | null, now: string, extras?: RunTransitionExtras): Promise<boolean>;

  getJob(jobId: string): Promise<RunJob | null>;
  // 終わりの状態でないジョブ（見回りが引く）
  listActiveJobs(): Promise<RunJob[]>;
  // ジョブを書き換える。from を渡すと、今の状態がそのどれかのときだけ書く。手元のマシンが受け取ったジョブを
  // 終わりの状態へ移すときは、そのマシンの枠も同じ書き込みで減らす（I50）
  updateJob(jobId: string, patch: Partial<RunJob>, from?: RunJob["state"][]): Promise<boolean>;
  // local の pending のジョブを running にし、手元のマシンの枠を 1 増やす。枠が maxJobs 以上なら false（I50）
  claimLocalJob(jobId: string, runnerId: string, maxJobs: number, now: string): Promise<boolean>;

  // ---- 開発ループ（#2347） ----
  // ループの行とマイルストーンの持ち分を 1 回で書く。持ち分が既にあれば、重なった題を返して書かない（I45）
  // idempotency があれば作成の冪等の鍵の行も同じトランザクションで書き、期限の中の同じ鍵があれば duplicate（#2384 の I14）
  createLoop(loop: DevLoop, idempotency?: IdempotencyClaim): Promise<"created" | "duplicate" | { conflict: string }>;
  // 作成の冪等の鍵の行が作った識別子。期限（ttl）を過ぎた行は、残っていても無いものとして扱う
  getIdempotent(ref: IdempotencyRef, now: string): Promise<string | null>;
  getLoop(accountId: string, loopId: string): Promise<DevLoop | null>;
  // アカウントのループの新しい順
  listLoops(accountId: string): Promise<DevLoop[]>;
  // ループの周回の実行（開始の書き込みで足すループの実行の行から引く。アカウントの全実行を読まない）
  listLoopRuns(accountId: string, loopId: string): Promise<SprintRun[]>;
  // 条件つきでループの行を書く（終わりの状態へ移るときは持ち分も消す）
  writeLoop(write: LoopWrite): Promise<boolean>;
  // マイルストーンの持ち分（強い整合）。無ければ null
  getMilestoneShare(accountId: string, repositoryId: number, title: string): Promise<{ loopId: string; title: string } | null>;
  // リポジトリの課題の持ち分（強い整合。決定 11）
  listIssueShares(accountId: string, repositoryId: number): Promise<{ number: number; sprintRunId: string }[]>;
  // 次の周回を待つループ（between_laps で、待ちの理由が user_start でないもの）の古い順
  // accountId を渡すとそのアカウントの待ちだけを返す（省くとサービス全体。見回り用）
  listWaitingLoops(accountId?: string): Promise<DevLoop[]>;

  // ---- 移行（決定 12） ----
  // 移行の済みの行があるか（強い整合）
  isMigrated(): Promise<boolean>;
  setMigrated(): Promise<void>;
  // legacy が空の終わりの状態でない実行
  listUnbackfilledRuns(): Promise<SprintRun[]>;
  // 実行を古い版として legacyCount と課題の持ち分へ写す。実行が終わりの状態か legacy が空でなければ false
  backfillRun(run: SprintRun, issues: number[]): Promise<boolean>;

  // ---- Factory のオーガナイザー（#2439） ----
  getOrganizer(accountId: string, repositoryId: number): Promise<OrganizerRoot | null>;
  // 根（と、渡せばまとめた配布・記録）を条件つきで書く（I56・I67）。条件で落ちれば false
  writeOrganizer(write: OrganizerWrite): Promise<boolean>;
  // まとめた配布・配布の実行とジョブ・根・記録を 1 回で書く（I57・I62）。条件で落ちれば false
  composeRelease(input: ComposeRelease): Promise<boolean>;
  listOrgRequests(accountId: string, repositoryId: number): Promise<OrgRequest[]>;
  // 答えがまだ無いときだけ書く（I58）。書いたら true
  answerRequest(accountId: string, repositoryId: number, requestId: string, answer: ReleaseAnswer): Promise<boolean>;
  // 答えを届け終えた（見回りの索引から外す）
  markDelivered(accountId: string, repositoryId: number, requestId: string, at: string): Promise<void>;
  getBatch(accountId: string, repositoryId: number, seq: number): Promise<ReleaseBatch | null>;
  // 新しい順
  listBatches(accountId: string, repositoryId: number, limit: number): Promise<ReleaseBatch[]>;
  appendOrgLog(log: OrgLog): Promise<void>;
  // 時刻の古い順
  listOrgLogs(accountId: string, repositoryId: number): Promise<OrgLog[]>;
  // 流れているまとめた配布を持つ根か、答えを届け終えていない依頼のあるリポジトリ（見回りが引く）
  listOrganizerActive(): Promise<{ accountId: string; repositoryId: number }[]>;
}
