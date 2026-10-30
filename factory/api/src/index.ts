// 他の子課題（#2171・#2186・#2174・#2189）へ公開する入口。使うのはここから export したものだけ
import type { Hono } from "hono";
import { AccountService } from "./accounts/service.ts";
import { depsFromEnv } from "./handlers/api.ts";
import { verifyAuthorization } from "./auth/verify.ts";
import type { AuthEnv } from "./auth/resolve.ts";
import type { AccountContext, Caller, Plan } from "./accounts/model.ts";
import type { JobFinishedEvent } from "@ndf-factory/contracts";
import { appendControlEventTo } from "./progress/append.ts";
import { progressStoreFromEnv } from "./handlers/ingest.ts";
import { runJobTokenVerifierFromEnv, type VerifiedRunJob } from "./progress/run-job-token.ts";

export { getLimits, judgeLimits, LIMITS_TABLE } from "./accounts/limits.ts";
export type { CurrentUsage, Judgment, LimitReason, Limits } from "./accounts/limits.ts";
export type { AccountContext, Caller, ExecutionMode, Plan, SeatType } from "./accounts/model.ts";
export { ApiError, AuthError, ForbiddenError, PlanError } from "./errors.ts";
export { createApiApp } from "./handlers/api.ts";
export { requireCaller, resolveFromRequest } from "./auth/resolve.ts";
export type { AuthEnv };
export type ApiApp = Hono<AuthEnv>;

// Authorization の値を検証し、利用者を返す。失敗は 401 の AuthError
export function verifyRequest(authorization: string | undefined | null): Promise<Caller> {
  return verifyAuthorization(depsFromEnv().verifier, authorization);
}

// 利用者とアカウントの ID（省くと個人のアカウント）から文脈を返す。権限が無ければ 403 の ForbiddenError
export function resolveAccount(caller: Caller, accountId?: string): Promise<AccountContext> {
  return depsFromEnv().accounts.resolveAccount(caller, accountId);
}

// アカウントのプランを変える（#2174 が決済の後に呼ぶ）。知らないプラン・I13 に反する組は PlanError
export function setPlan(accountId: string, plan: Plan): Promise<void> {
  return depsFromEnv().accounts.setPlan(accountId, plan);
}

export { AccountService };

// 進捗の記録と実行ジョブの token（#2171）。契約の型は @ndf-factory/contracts から写して出す
export type * from "@ndf-factory/contracts";
export { createIngestApp } from "./progress/app.ts";
export type { IngestDeps, IngestLogEntry, IngestLogger } from "./progress/app.ts";
export { ProgressEventError } from "./progress/errors.ts";
export {
  createRunJobTokenVerifier,
  RunJobTokenError,
  signRunJobToken,
} from "./progress/run-job-token.ts";
export type {
  RunJobSigningKey,
  RunJobTokenClaims,
  RunJobTokenErrorCode,
  RunJobTokenVerifier,
  VerifiedRunJob,
} from "./progress/run-job-token.ts";
export type { SavedAnswer, SavedAnswerReader } from "./progress/saved-answers.ts";
export type { ProgressEventRecord, ProgressEventStore } from "./progress/store.ts";
export { createRunJobTokenKit } from "./testing/run-job-tokens.ts";
export type { RunJobTokenKit } from "./testing/run-job-tokens.ts";

// 実行ジョブの token を確かめる（受け口と #2173 が呼ぶ）。公開鍵の集合は RUN_JOB_TOKEN_VERIFY_KEYS_SECRET_ARN から
// 読み、5 分ごとに読み直す。無効なら RunJobTokenError（呼ぶ側が 401 にする）、秘密を読めなければほかの例外（503）
export function verifyRunJobToken(token: string): Promise<VerifiedRunJob> {
  return runJobTokenVerifierFromEnv()(token);
}

// 実行の制御（#2186）が job.finished（source は control、鍵は <job_id>:finished）を受け口と同じ重複の拒否で直に書く。
// { written: false } は同じ鍵（ランナーの分）が既にある。定義・照合に合わなければ ProgressEventError
export function appendControlEvent(event: JobFinishedEvent): Promise<{ written: boolean }> {
  return appendControlEventTo(progressStoreFromEnv(), event);
}
