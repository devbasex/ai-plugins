// 出所の照合（I25〜I28）。契約の定義の検査を通った事象に当て、経路の出所・書ける種類・識別子・冪等の鍵の形を照らす
import type {
  EventSource,
  EventType,
  GateAnsweredEvent,
  IssueRequestedEvent,
  IssueRequestResolvedEvent,
  ProgressEvent,
  ReleaseAnsweredEvent,
  ReleasePresentedEvent,
  ReleaseProgressEvent,
  ReleaseRequestedEvent,
  ReleaseSettledEvent,
  StepRecordedEvent,
} from "@ndf-factory/contracts";
import { releasePresentedKey, releaseProgressKey, releaseSettledKey } from "@ndf-factory/contracts";
import { repoKey } from "./github.ts";
import type { VerifiedRunJob } from "./run-job-token.ts";
import type { SavedAnswerReader } from "./saved-answers.ts";

// 出所ごとに書ける種類（I26）
export const WRITABLE_TYPES: Record<EventSource, ReadonlySet<EventType>> = {
  runner: new Set<EventType>([
    "job.started",
    "phase.changed",
    "step.recorded",
    "attention",
    "gate.reached",
    "usage.recorded",
    "job.finished",
    "decision.delegated",
    "issue.requested",
    // 区間とループの事象（#2347）。ランナーが送り、周回・判断待ち・停止の理由として読まれる
    "section.started",
    "loop.lap_closed",
    "loop.stopped",
    // リリースの依頼とリリースの実行の事象（#2439）。Factory のオーガナイザーが停止の後の処理で読む
    "release.requested",
    "release.presented",
    "release.progress",
    "release.settled",
  ]),
  api: new Set<EventType>(["gate.answered"]),
  github: new Set<EventType>(["pr.updated", "ci.updated"]),
  control: new Set<EventType>(["job.finished", "issue.request_resolved", "release.answered"]),
};

const canWrite = (source: EventSource, event: ProgressEvent) =>
  event.source === source && WRITABLE_TYPES[source].has(event.type);

// 実行ジョブの終わりの冪等の鍵。ランナーと実行の制御が同じ鍵を使い、先の 1 つだけが残る
export const finishedKey = (jobId: string) => `${jobId}:finished`;

// リリースの事象の冪等の鍵（#2439）。依頼は依頼の id、リリースの実行の事象は実行の識別子を頭にする（式は契約が持ち、
// ランナーの送信と同じ式で照らす）。ランナーが送り直しても 1 件になる
export const releaseRequestedKey = (requestId: string) => requestId;
export { releasePresentedKey, releaseProgressKey, releaseSettledKey };
export const releaseAnsweredKey = (seq: number, sprintRunId: string) => `${seq}:${sprintRunId}`;

function releaseKeyOf(event: ProgressEvent): string | null {
  switch (event.type) {
    case "release.requested":
      return releaseRequestedKey((event as ReleaseRequestedEvent).payload.request_id);
    case "release.presented":
      return releasePresentedKey(event.sprint_run_id, (event as ReleasePresentedEvent).payload.k);
    case "release.progress":
      return releaseProgressKey(event.sprint_run_id, (event as ReleaseProgressEvent).payload.stage);
    case "release.settled":
      return releaseSettledKey(event.sprint_run_id, (event as ReleaseSettledEvent).payload.result);
    default:
      return null;
  }
}

// ランナーの冪等の鍵の形（I27）: job.finished は <job_id>:finished、リリースの事象は releaseKeyOf、ほかは <job_id>:<seq>（seq は 1 以上）
function isRunnerKey(event: ProgressEvent, jobId: string): boolean {
  if (event.type === "job.finished") return event.idempotency_key === finishedKey(jobId);
  const releaseKey = releaseKeyOf(event);
  if (releaseKey !== null) return event.idempotency_key === releaseKey;
  const prefix = `${jobId}:`;
  return event.idempotency_key.startsWith(prefix) && /^[1-9][0-9]*$/.test(event.idempotency_key.slice(prefix.length));
}

// step.recorded の Pull Request は token の repo のものだけ（I37、#2216）。大文字と小文字は問わない
function isAllowedPullRequest(event: ProgressEvent, job: VerifiedRunJob): boolean {
  if (event.type !== "step.recorded") return true;
  const pr = (event as StepRecordedEvent).payload.pull_request;
  return pr === undefined || repoKey(pr.repo) === repoKey(job.repo);
}

// issue.requested の起票先は token の repo と違う（I45、#2216）。実行のリポジトリへはランナーが自分の権限で起票する
function isAllowedIssueRequest(event: ProgressEvent, job: VerifiedRunJob): boolean {
  if (event.type !== "issue.requested") return true;
  return repoKey((event as IssueRequestedEvent).payload.repo) !== repoKey(job.repo);
}

// runner: token の実行ジョブと事象の job_id・sprint_run_id が一致し、鍵がそのジョブの形で、
// Pull Request の参照がその実行に許可したリポジトリのもので、起票の依頼が別のリポジトリ宛てである（I25〜I27・I37・I45）
export function authorizeRunner(event: ProgressEvent, job: VerifiedRunJob): boolean {
  return (
    canWrite("runner", event) &&
    event.job_id === job.jobId &&
    event.sprint_run_id === job.sprintRunId &&
    isRunnerKey(event, job.jobId) &&
    isAllowedPullRequest(event, job) &&
    isAllowedIssueRequest(event, job)
  );
}

// 起票の依頼の結果の冪等の鍵。依頼 1 件に結果は 1 件だけ残る（I48、#2216）
export const resolvedKey = (requestKey: string) => `${requestKey}:resolved`;

// control: job.finished は鍵が <job_id>:finished（決定 9）。issue.request_resolved は request_key が
// そのジョブの依頼の鍵（<job_id>:<seq>）で、鍵が <request_key>:resolved（#2216）
// release.answered はジョブの外の事象（job_id は null）で、鍵が <seq>:<sprint_run_id>（#2439）
export function authorizeControl(event: ProgressEvent): boolean {
  if (event.type === "release.answered") {
    const { seq } = (event as ReleaseAnsweredEvent).payload;
    return canWrite("control", event) && event.job_id === null && event.idempotency_key === releaseAnsweredKey(seq, event.sprint_run_id);
  }
  if (!canWrite("control", event) || event.job_id === null) return false;
  if (event.type === "job.finished") return event.idempotency_key === finishedKey(event.job_id);
  const { request_key } = (event as IssueRequestResolvedEvent).payload;
  const prefix = `${event.job_id}:`;
  return (
    request_key.startsWith(prefix) &&
    /^[1-9][0-9]*$/.test(request_key.slice(prefix.length)) &&
    event.idempotency_key === resolvedKey(request_key)
  );
}

// api: gate.answered の識別子・スプリントの実行・種類・答えた人・承認か差し戻しかが保存済みの答えとすべて一致する（I28）。
// 未保存は false。読めない失敗は投げる（呼ぶ側が 503 にする）
export async function authorizeApi(event: ProgressEvent, savedAnswers: SavedAnswerReader): Promise<boolean> {
  if (!canWrite("api", event) || event.job_id !== null) return false;
  const { payload } = event as GateAnsweredEvent;
  if (event.idempotency_key !== `api:${payload.approval_request_id}`) return false;
  const saved = await savedAnswers(payload.approval_request_id);
  return (
    saved !== null &&
    saved.approvalRequestId === payload.approval_request_id &&
    saved.sprintRunId === event.sprint_run_id &&
    saved.kind === payload.kind &&
    saved.answeredByAccountId === payload.answered_by_account_id &&
    saved.decision === payload.decision
  );
}

// github: 翻訳した事象だけを残す。経路と種類だけを照らす
export function authorizeGithub(event: ProgressEvent): boolean {
  return canWrite("github", event) && event.job_id === null;
}

// IAM のロールのアカウントとロールの名前（決定 12）。
// 呼び出し元は arn:aws:sts::<アカウント>:assumed-role/<ロールの名前>/<セッション>、
// 設定は arn:aws:iam::<アカウント>:role/<パス>/<ロールの名前> で届く。セッションの名前とパスは比べない
const ASSUMED_ROLE = /^arn:(aws[a-z-]*):sts::(\d{12}):assumed-role\/([^/]+)\/[^/]+$/;
const ROLE = /^arn:(aws[a-z-]*):iam::(\d{12}):role\/(?:.+\/)?([^/]+)$/;

export function isCallerRole(callerArn: string, roleArn: string): boolean {
  const caller = ASSUMED_ROLE.exec(callerArn);
  const role = ROLE.exec(roleArn);
  if (!caller || !role) return false;
  return caller[1] === role[1] && caller[2] === role[2] && caller[3] === role[3];
}
