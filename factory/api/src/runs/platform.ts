// 実行基盤の口。ECS のタスク・token の置き場・状態の置き場の署名つき URL を、実行の制御の語で受ける。テストは偽物
import type { RunJobInstruction } from "@ndf-factory/contracts";
import type { ExecutionMode } from "../accounts/model.ts";

// STOPPING は止まる途中（コンテナがまだ動きうる）。STOPPED を確かめるまでジョブを確定しない
export type TaskStatus = "PENDING" | "RUNNING" | "STOPPING" | "STOPPED" | "MISSING";

export interface SignedUrl {
  url: string;
  expiresAt: string;
}

export interface RunnerPlatform {
  // ジョブの token を ndf-factory/run-jobs/<job_id> へ書き、置き場の ARN を返す
  putJobToken(jobId: string, token: string): Promise<string>;
  deleteJobToken(secretArn: string): Promise<void>;
  // 形態ごとのタスク定義で起動する（決定 11）。startedBy にジョブの ID を入れ、タスクの ARN を返す
  runTask(mode: Exclude<ExecutionMode, "local">, instruction: RunJobInstruction, startedBy: string): Promise<string>;
  stopTask(taskArn: string, reason: string): Promise<void>;
  describeTask(taskArn: string): Promise<TaskStatus>;
  // 状態の置き場（sprint-runs/<sprint_run_id>/ndf-state.tar.gz）の 15 分の署名つき URL。get で無ければ null
  stateUrl(sprintRunId: string, op: "put" | "get"): Promise<SignedUrl | null>;
}
