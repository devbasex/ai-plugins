// 他の子課題（#2171・#2186・#2174・#2189）へ公開する入口。使うのはここから export したものだけ
import type { Hono } from "hono";
import { AccountService } from "./accounts/service.ts";
import { depsFromEnv } from "./handlers/api.ts";
import { verifyAuthorization } from "./auth/verify.ts";
import type { AuthEnv } from "./auth/resolve.ts";
import type { AccountContext, Caller, Plan } from "./accounts/model.ts";

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
