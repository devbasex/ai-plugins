// 実行の表（RUNS_TABLE_NAME）の口。条件つきの書き込みを操作の単位で持ち、同時の 2 本でも不変条件が崩れない形にする
import type { CredentialRow, FailureReason, Registration, RunJob, RunState, Slots, SprintRun } from "./model.ts";

export interface RunsTable {
  // リポジトリの登録（I32・I36）
  getRegistration(accountId: string, repositoryId: number): Promise<Registration | null>;
  listRegistrations(accountId: string): Promise<Registration[]>;
  // 同じアカウントに同じ ID があれば false（I32）
  putRegistration(registration: Registration): Promise<boolean>;
  setRegistrationName(accountId: string, repositoryId: number, fullName: string): Promise<void>;
  // リポジトリの枠が 0 でなければ in_use で書かない（I36）
  deleteRegistration(accountId: string, repositoryId: number): Promise<"deleted" | "not_found" | "in_use">;

  // 持ち込んだキー（値は持たない。I38）
  getCredential(accountId: string): Promise<CredentialRow | null>;
  putCredential(row: CredentialRow): Promise<void>;
  deleteCredential(accountId: string): Promise<void>;

  // 実行の枠（I34）
  getSlots(accountId: string, repositoryId: number): Promise<Slots>;

  // 実行・最初のジョブ・枠を 1 回で書く。枠が expected と違えば false（決定 5）
  createRun(run: SprintRun, job: RunJob, expected: Slots): Promise<boolean>;
  getRun(sprintRunId: string): Promise<SprintRun | null>;
  // アカウントの実行の新しい順
  listRuns(accountId: string, limit: number): Promise<SprintRun[]>;
  setCancelRequested(sprintRunId: string): Promise<void>;
  // 終わりの状態でない実行を running にする（starting のときだけ）
  markRunRunning(sprintRunId: string, now: string): Promise<void>;
  // 承認待ちの実行に resume のジョブを足す。承認待ちで動くジョブが無いときだけ書く（I9）
  addResumeJob(job: RunJob, now: string): Promise<boolean>;
  // 動くジョブを外し、実行を承認待ちへ移す（終わりの状態でないときだけ）
  awaitApproval(sprintRunId: string, jobId: string, now: string): Promise<boolean>;
  // 終わりの状態へ移し、2 つの枠を 1 ずつ減らす。既に終わりの状態なら false（I34）
  finishRun(sprintRunId: string, state: RunState, reason: FailureReason | null, now: string): Promise<boolean>;

  getJob(jobId: string): Promise<RunJob | null>;
  // 終わりの状態でないジョブ（見回りが引く）
  listActiveJobs(): Promise<RunJob[]>;
  // ジョブを書き換える。from を渡すと、今の状態がそのどれかのときだけ書く
  updateJob(jobId: string, patch: Partial<RunJob>, from?: RunJob["state"][]): Promise<boolean>;
}
