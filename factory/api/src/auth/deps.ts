// API の認証と解決の依存を環境変数から作る
import { AccountService } from "../accounts/service.ts";
import { DynamoAccountStore } from "../accounts/store.ts";
import { DynamoAuditLog } from "../audit/dynamo-log.ts";
import { StructuredLogAudit } from "../audit/structured-log.ts";
import { DelegationService } from "../delegations/service.ts";
import { DynamoDelegationStore } from "../delegations/store.ts";
import { requireEnv } from "../env.ts";
import type { AuthDeps } from "./resolve.ts";
import { createAccessTokenVerifier } from "./verify.ts";

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
