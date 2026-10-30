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

export interface LogEntry {
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
): Hono<AuthEnv> {
  const app = new Hono<AuthEnv>();
  app.use("*", async (c, next) => {
    await next();
    log({
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
  app.use("*", requireCaller(deps));
  app.onError((err, c) => {
    const known = err instanceof ApiError ? err : new AccountUnavailableError();
    return c.json(known.body(), known.status);
  });
  registerMe(app);
  registerTeams(app);
  registerAccounts(app);
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
  };
  return cached;
}

// API Gateway の HTTP API（payload 2.0）から呼ばれる Lambda の handler
export const handler = handle(createApiApp(depsFromEnv));
