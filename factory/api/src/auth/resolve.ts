import type { Context, MiddlewareHandler } from "hono";
import type { AccountService } from "../accounts/service.ts";
import type { AccountContext, Caller } from "../accounts/model.ts";
import { verifyAuthorization, type AccessTokenVerifier } from "./verify.ts";

export interface AuthDeps {
  verifier: AccessTokenVerifier;
  accounts: AccountService;
}

// 操作の登録の層が、操作の記録へ渡すために置く値（#2384）。対象の ID は許可した鍵の値だけ
export interface OperationTrace {
  operationId: string;
  accountId: string | null;
  targetIds: string[];
  // 失敗した要素の誤りの code（重ねない）
  reasons: string[];
}

export type AuthEnv = { Variables: { caller: Caller; auth: AuthDeps; operation?: OperationTrace } };

// 経路の前に token を毎回検証し、利用者を文脈に置く中間処理（E3）。
// #2171・#2186・#2174 の経路も同じ中間処理を通す
export function requireCaller(deps: () => AuthDeps): MiddlewareHandler<AuthEnv> {
  return async (c, next) => {
    const d = deps();
    c.set("auth", d);
    c.set("caller", await verifyAuthorization(d.verifier, c.req.header("Authorization")));
    await next();
  };
}

// 経路のアカウントの ID（省くと個人のアカウント）から、権限つきの文脈を返す。権限が無ければ 403（I18）
export function resolveFromRequest(c: Context<AuthEnv>, accountId?: string): Promise<AccountContext> {
  return c.get("auth").accounts.resolveAccount(c.get("caller"), accountId);
}
