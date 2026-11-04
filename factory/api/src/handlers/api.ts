import { Hono } from "hono";
import { handle } from "hono/aws-lambda";
import { cors } from "hono/cors";
import { ApiError, AccountUnavailableError } from "../errors.ts";
import { AccountService } from "../accounts/service.ts";
import { DynamoAccountStore } from "../accounts/store.ts";
import { createAccessTokenVerifier } from "../auth/verify.ts";
import { requireCaller, type AuthDeps, type AuthEnv } from "../auth/resolve.ts";
import { registerMe } from "../routes/me.ts";
import { registerTeams } from "../routes/teams.ts";
import { registerAccounts } from "../routes/accounts.ts";
import { registerDelegations } from "../routes/delegations.ts";
import { DelegationService } from "../delegations/service.ts";
import { DynamoDelegationStore } from "../delegations/store.ts";
import { recordOperations, requestIdOf } from "../audit/middleware.ts";
import { DynamoAuditLog } from "../audit/dynamo-log.ts";
import { StructuredLogAudit } from "../audit/structured-log.ts";
import { registerMcp } from "../mcp/server.ts";
import { registerFactory, type FactoryDeps } from "../routes/factory.ts";
import { factoryDepsFromEnv } from "../runs/deps.ts";

export interface LogEntry {
  // 操作の記録の record_id と同じ値（同じ要求の記録とログの行をたどる）
  request_id: string;
  user_id: string | null;
  method: string;
  path: string;
  status: number;
}

export type Logger = (entry: LogEntry) => void;

// ログは利用者の ID・経路・状態コードだけを出す。token とメールアドレスは出さない
export const jsonLogger: Logger = (entry) => console.log(JSON.stringify(entry));

// 画面のオリジン（FACTORY_WEB_URL のスキーム・ホスト・ポート）。無いか URL でなければ null で、どのオリジンも許さない
export function webOriginFromEnv(): string | null {
  const url = process.env.FACTORY_WEB_URL;
  if (!url) return null;
  try {
    return new URL(url).origin;
  } catch {
    return null;
  }
}

// API の入口。#2171・#2186・#2174 は、返った app に経路を足し、同じ認証と解決の中間処理を共有する（決定 11）
export function createApiApp(
  deps: () => AuthDeps,
  log: Logger = jsonLogger,
  webOrigin: () => string | null = webOriginFromEnv,
  factory: (() => FactoryDeps) | null = null,
): Hono<AuthEnv> {
  const app = new Hono<AuthEnv>();
  app.use("*", async (c, next) => {
    c.set("requestId", requestIdOf(c));
    await next();
    log({
      request_id: c.get("requestId")!,
      user_id: c.get("caller")?.userId ?? null,
      method: c.req.method,
      path: c.req.path,
      status: c.res.status,
    });
  });
  // 画面と API は別のオリジン。preflight（Authorization を持たない OPTIONS）を認証より先に返す
  app.use(
    "*",
    cors({
      origin: (origin) => (origin === webOrigin() ? origin : null),
      allowHeaders: ["Authorization", "Content-Type"],
      allowMethods: ["GET", "POST", "PUT", "PATCH", "DELETE"],
    }),
  );
  // 操作の記録は認証より外に置き、401 で断った要求も記録する（I7）
  app.use("*", recordOperations(() => deps().audit, (line) => console.log(JSON.stringify(line))));
  app.use("*", requireCaller(deps));
  app.onError((err, c) => {
    const known = err instanceof ApiError ? err : new AccountUnavailableError();
    return c.json(known.body(), known.status);
  });
  registerMe(app);
  registerTeams(app);
  registerAccounts(app);
  registerDelegations(app);
  if (factory) registerFactory(app, factory);
  // MCP の受け口。依存は同じアプリへの内部の要求だけ（#2384 の I15）
  registerMcp(app, (path, init, env) => app.request(path, init, env), webOrigin);
  return app;
}

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`環境変数 ${name} がありません`);
  return value;
}

let cached: AuthDeps | null = null;

// 環境変数から依存を作る。起動のたびに 1 回だけ作る
export function depsFromEnv(): AuthDeps {
  cached ??= {
    verifier: createAccessTokenVerifier({
      userPoolId: requireEnv("COGNITO_USER_POOL_ID"),
      clientId: requireEnv("COGNITO_CLIENT_ID"),
    }),
    accounts: new AccountService({ store: new DynamoAccountStore(requireEnv("ACCOUNTS_TABLE_NAME")) }),
    // 委任の資格情報の表（インフラが DELEGATIONS_TABLE_NAME で渡す）。無い間は委任の資格情報を 401 にし、委任の操作を 503 にする
    delegations: process.env.DELEGATIONS_TABLE_NAME ? new DelegationService({ store: new DynamoDelegationStore(process.env.DELEGATIONS_TABLE_NAME) }) : null,
    // 操作の記録の表（インフラが AUDIT_TABLE_NAME で渡す）。無い間は記録を構造化ログへ出す
    audit: process.env.AUDIT_TABLE_NAME ? new DynamoAuditLog(process.env.AUDIT_TABLE_NAME) : new StructuredLogAudit(),
  };
  return cached;
}

// API Gateway の HTTP API（payload 2.0）から呼ばれる Lambda の handler
export const handler = handle(createApiApp(depsFromEnv, jsonLogger, webOriginFromEnv, factoryDepsFromEnv));
