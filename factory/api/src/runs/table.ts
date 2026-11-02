// 実行の表（RUNS_TABLE_NAME）の口。条件つきの書き込みを操作の単位で持ち、同時の 2 本でも不変条件が崩れない形にする
import type {
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

// 終わりへの移り・承認待ち・リセット待ちに添える書き込み（決定 2。ループは実行と同じトランザクションで書く）
export interface RunTransitionExtras {
  finishReason?: FinishReason | null;
  loop?: LoopWrite;
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
  addResumeJob(job: RunJob, now: string, opts?: { from?: "awaiting_approval" | "awaiting_reset"; loop?: LoopWrite }): Promise<boolean>;
  // 動くジョブを外し、実行を承認待ちへ移す（終わりの状態でないときだけ）
  awaitApproval(sprintRunId: string, jobId: string, now: string, extras?: RunTransitionExtras): Promise<boolean>;
  // 動くジョブを外し、実行をリセット待ちへ移す（local の利用上限。#2347 の決定 7）
  awaitReset(sprintRunId: string, jobId: string, resumeAt: string, now: string, extras?: RunTransitionExtras): Promise<boolean>;
  // リセット時刻が before 以前のリセット待ちの実行（見回りが引く）
  listResetRuns(before: string): Promise<SprintRun[]>;
  // 終わりの状態へ移し、2 つの枠を 1 ずつ減らす。課題の持ち分を消し、古い版の実行なら legacyCount も減らす。
  // 既に終わりの状態なら false（I34・I47・I48）
  finishRun(sprintRunId: string, state: RunState, reason: FailureReason | null, now: string, extras?: RunTransitionExtras): Promise<boolean>;

  getJob(jobId: string): Promise<RunJob | null>;
  // 終わりの状態でないジョブ（見回りが引く）
  listActiveJobs(): Promise<RunJob[]>;
  // ジョブを書き換える。from を渡すと、今の状態がそのどれかのときだけ書く。手元のランナーが受け取ったジョブを
  // 終わりの状態へ移すときは、そのランナーの枠も同じ書き込みで減らす（I50）
  updateJob(jobId: string, patch: Partial<RunJob>, from?: RunJob["state"][]): Promise<boolean>;
  // local の pending のジョブを running にし、手元のランナーの枠を 1 増やす。枠が maxJobs 以上なら false（I50）
  claimLocalJob(jobId: string, runnerId: string, maxJobs: number, now: string): Promise<boolean>;

  // ---- 開発ループ（#2347） ----
  // ループの行とマイルストーンの持ち分を 1 回で書く。持ち分が既にあれば、重なった題を返して書かない（I45）
  createLoop(loop: DevLoop): Promise<"created" | { conflict: string }>;
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
  listWaitingLoops(): Promise<DevLoop[]>;

  // ---- 移行（決定 12） ----
  // 移行の済みの行があるか（強い整合）
  isMigrated(): Promise<boolean>;
  setMigrated(): Promise<void>;
  // legacy が空の終わりの状態でない実行
  listUnbackfilledRuns(): Promise<SprintRun[]>;
  // 実行を古い版として legacyCount と課題の持ち分へ写す。実行が終わりの状態か legacy が空でなければ false
  backfillRun(run: SprintRun, issues: number[]): Promise<boolean>;
}
