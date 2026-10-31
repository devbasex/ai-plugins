// 実行の制御と画面向けの経路の依存を環境変数から作る。起動のたびに 1 回だけ作り、秘密は ARN からだけ読む
import { createHash } from "node:crypto";
import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";
import type { JWK } from "jose";
import { CredentialService, AwsSecrets } from "../credentials/service.ts";
import { GitHubApp } from "../github/app.ts";
import { appendControlEventTo } from "../progress/append.ts";
import { DynamoProgressReader } from "../progress/read.ts";
import type { RunJobSigningKey } from "../progress/run-job-token.ts";
import { progressStoreFromEnv } from "../handlers/ingest.ts";
import { RepositoryService } from "../repositories/service.ts";
import type { FactoryDeps } from "../routes/factory.ts";
import { bedrockCandidateModel } from "./candidates.ts";
import { RunControl, runControlConfig } from "./control.ts";
import { DynamoRunsTable } from "./dynamo-table.ts";
import { EcsRunnerPlatform } from "./ecs.ts";

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`環境変数 ${name} がありません`);
  return value;
}

const list = (name: string) => requireEnv(name).split(",").map((s) => s.trim()).filter(Boolean);

// 秘密を 1 回だけ読む。読めなければ次の呼び出しで読み直す。値をログと応答に出さない
function secretOnce(arnEnv: string, client: SecretsManagerClient): () => Promise<string> {
  let cached: Promise<string> | null = null;
  return () => {
    cached ??= client.send(new GetSecretValueCommand({ SecretId: requireEnv(arnEnv) })).then((out) => {
      if (!out.SecretString) throw new Error(`${arnEnv} の秘密が読めません`);
      return out.SecretString;
    });
    cached.catch(() => {
      cached = null;
    });
    return cached;
  };
}

// NDF の版（決定 3）。正式版は plugin.json の version、開発版は <version>@<コミットの SHA>
export function ndfVersionFromMarketplace(fetchImpl: typeof fetch = fetch): () => Promise<string> {
  return async () => {
    const channel = process.env.NDF_CHANNEL || "main";
    const repo = process.env.NDF_MARKETPLACE || "devbasex/ai-plugins";
    const raw = await fetchImpl(`https://raw.githubusercontent.com/${repo}/${channel}/plugins/ndf/.claude-plugin/plugin.json`);
    if (!raw.ok) throw new Error(`NDF の版を読めません（${raw.status}）`);
    const version = ((await raw.json()) as { version?: unknown }).version;
    if (typeof version !== "string" || version === "") throw new Error("NDF の版を読めません");
    if (channel === "main") return version;
    const commit = await fetchImpl(`https://api.github.com/repos/${repo}/commits/${channel}`, { headers: { Accept: "application/vnd.github.sha" } });
    if (!commit.ok) throw new Error(`NDF のコミットを読めません（${commit.status}）`);
    return `${version}@${(await commit.text()).trim()}`;
  };
}

let cached: (FactoryDeps & { platform: EcsRunnerPlatform }) | null = null;

export function factoryDepsFromEnv(): FactoryDeps & { platform: EcsRunnerPlatform } {
  if (cached) return cached;
  const sm = new SecretsManagerClient({});
  const table = new DynamoRunsTable(requireEnv("RUNS_TABLE_NAME"));
  const clientSecret = secretOnce("GITHUB_APP_CLIENT_SECRET_ARN", sm);
  const github = new GitHubApp({
    appId: requireEnv("GITHUB_APP_ID"),
    slug: requireEnv("GITHUB_APP_SLUG"),
    clientId: requireEnv("GITHUB_APP_CLIENT_ID"),
    clientSecret,
    privateKeyPem: secretOnce("GITHUB_APP_PRIVATE_KEY_SECRET_ARN", sm),
  });
  const platform = new EcsRunnerPlatform({
    clusterArn: requireEnv("RUNNER_CLUSTER_ARN"),
    subnetIds: list("RUNNER_SUBNET_IDS"),
    securityGroupIds: list("RUNNER_SECURITY_GROUP_IDS"),
    managedTaskDefinitionArn: requireEnv("RUNNER_TASK_DEFINITION_ARN"),
    byokTaskDefinitionArn: requireEnv("RUNNER_BYOK_TASK_DEFINITION_ARN"),
    stateBucket: requireEnv("RUN_STATE_BUCKET"),
  });
  const signing = secretOnce("RUN_JOB_TOKEN_SIGNING_KEY_SECRET_ARN", sm);
  const signingKey = async (): Promise<RunJobSigningKey> => {
    const parsed = JSON.parse(await signing()) as { kid?: string; jwk?: JWK };
    if (!parsed.kid || !parsed.jwk) throw new Error("実行ジョブの token の発行の鍵が読めません");
    return { kid: parsed.kid, privateKey: parsed.jwk };
  };
  const control = new RunControl({
    table,
    github,
    platform,
    reader: new DynamoProgressReader(requireEnv("PROGRESS_EVENTS_TABLE_NAME"), requireEnv("PROGRESS_IDEMPOTENCY_TABLE_NAME")),
    appendControl: (e) => appendControlEventTo(progressStoreFromEnv(), e),
    signingKey,
    ndfVersion: ndfVersionFromMarketplace(),
    config: runControlConfig(process.env),
  });
  // 候補の証明の鍵は App のクライアントの秘密から用途を分けて作る
  const proofKey = async () => new Uint8Array(createHash("sha256").update(`ndf-factory:repositories:${await clientSecret()}`).digest());
  cached = {
    repositories: new RepositoryService({ table, github, proofKey }),
    credentials: new CredentialService(table, new AwsSecrets(sm)),
    control,
    table,
    github,
    candidateModel: bedrockCandidateModel(requireEnv("CANDIDATE_MODEL_ID")),
    platform,
  };
  return cached;
}
