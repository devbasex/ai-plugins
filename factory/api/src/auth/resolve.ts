import type { Context, MiddlewareHandler } from "hono";
import type { AccountService } from "../accounts/service.ts";
import type { AccountContext, Caller } from "../accounts/model.ts";
import type { AuditLog } from "../audit/log.ts";
import type { DelegationService } from "../delegations/service.ts";
import { AuthError } from "../errors.ts";
import { verifyAuthorization, type AccessTokenVerifier } from "./verify.ts";

export interface AuthDeps {
  verifier: AccessTokenVerifier;
  accounts: AccountService;
  // 委任の資格情報の照合（#2384）。無ければ委任の資格情報は 401
  delegations?: DelegationService | null;
  // 操作の記録の書き手（#2384）。無ければ記録しない（テストで記録を見ないとき）
  audit?: AuditLog | null;
}

// MCP の受け口が内部の要求に付ける印（app.request の第 3 引数の環境に置く）。外から届くヘッダーでは決めない（決定 13・I16）
export const MCP_INTERNAL = Symbol("ndf-factory.mcp-internal");

export function isMcpInternal(env: unknown): boolean {
  return typeof env === "object" && env !== null && (env as Record<symbol, unknown>)[MCP_INTERNAL] === true;
}

const DELEGATION_PREFIX = "Bearer ndfd_";

// Authorization の値を、委任の資格情報（頭が ndfd_）か Cognito の access token として検証する（E2）。どの失敗も 401
export async function verifyCaller(deps: AuthDeps, authorization: string | undefined, mcp: boolean): Promise<Caller> {
  if (authorization?.startsWith(DELEGATION_PREFIX)) {
    const verified = deps.delegations ? await deps.delegations.verify(authorization.slice("Bearer ".length)) : null;
    if (!verified) throw new AuthError();
    return {
      userId: verified.userId,
      idp: verified.idp,
      delegation: { credentialId: verified.credentialId, scope: verified.scope },
      via: mcp ? "mcp" : "api",
    };
  }
  // MCP の受け口は委任の資格情報だけを受ける
  if (mcp) throw new AuthError();
  return { ...(await verifyAuthorization(deps.verifier, authorization)), delegation: null, via: "screen" };
}

// 操作の登録の層が、操作の記録へ渡すために置く値（#2384）。対象の ID は許可した鍵の値だけ
export interface OperationTrace {
  operationId: string;
  accountId: string | null;
  targetIds: string[];
  // 失敗した要素の誤りの code（重ねない）
  reasons: string[];
}

export type AuthEnv = { Variables: { caller: Caller; auth: AuthDeps; operation?: OperationTrace; requestId?: string } };

// 経路の前に token を毎回検証し、利用者を文脈に置く中間処理（E3）。
// #2171・#2186・#2174 の経路も同じ中間処理を通す
export function requireCaller(deps: () => AuthDeps): MiddlewareHandler<AuthEnv> {
  return async (c, next) => {
    const d = deps();
    c.set("auth", d);
    c.set("caller", await verifyCaller(d, c.req.header("Authorization"), isMcpInternal(c.env)));
    await next();
  };
}

// 経路のアカウントの ID（省くと個人のアカウント）から、権限つきの文脈を返す。権限が無ければ 403（I18）
export function resolveFromRequest(c: Context<AuthEnv>, accountId?: string): Promise<AccountContext> {
  return c.get("auth").accounts.resolveAccount(c.get("caller"), accountId);
}
