// 進捗の受け口の Lambda の handler（API Gateway の HTTP API、payload 2.0）。
// POST /ingest/events・/ingest/api/events・/ingest/github の 3 つの経路を受ける（決定 4）
import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";
import { handle } from "hono/aws-lambda";
import { createIngestApp, type IngestDeps } from "../progress/app.ts";
import { runJobTokenVerifierFromEnv } from "../progress/run-job-token.ts";
import { savedAnswersFromEnv } from "../progress/saved-answers.ts";
import { DynamoProgressEventStore, type ProgressEventStore } from "../progress/store.ts";

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`環境変数 ${name} がありません`);
  return value;
}

let store: ProgressEventStore | null = null;

// 進捗の記録の保存。受け口と appendControlEvent が同じものを使う
export function progressStoreFromEnv(): ProgressEventStore {
  store ??= new DynamoProgressEventStore(
    requireEnv("PROGRESS_EVENTS_TABLE_NAME"),
    requireEnv("PROGRESS_IDEMPOTENCY_TABLE_NAME"),
  );
  return store;
}

let webhookSecret: Promise<string> | null = null;

// GitHub App の Webhook の秘密を 1 回だけ読む。読めなければ次の要求で読み直す。値をログと応答に出さない
function webhookSecretFromEnv(): Promise<string> {
  webhookSecret ??= new SecretsManagerClient({})
    .send(new GetSecretValueCommand({ SecretId: requireEnv("GITHUB_APP_WEBHOOK_SECRET_ARN") }))
    .then((out) => {
      if (!out.SecretString) throw new Error("Webhook の秘密が読めません");
      return out.SecretString;
    });
  webhookSecret.catch(() => {
    webhookSecret = null;
  });
  return webhookSecret;
}

let deps: IngestDeps | null = null;

// 環境変数から依存を作る。起動のたびに 1 回だけ作る
export function ingestDepsFromEnv(): IngestDeps {
  deps ??= {
    verifier: runJobTokenVerifierFromEnv(),
    store: progressStoreFromEnv(),
    savedAnswers: savedAnswersFromEnv(),
    webhookSecret: webhookSecretFromEnv,
    apiCallerRoleArn: requireEnv("INGEST_API_CALLER_ROLE_ARN"),
  };
  return deps;
}

export const handler = handle(createIngestApp(ingestDepsFromEnv));
