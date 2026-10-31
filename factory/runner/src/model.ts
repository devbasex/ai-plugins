// 形態ごとの claude の呼び方（受け入れ条件 29）。アクセスキーをイメージと環境変数に置かず、
// 子の環境にだけ必要な値を置く。managed はタスクの役割の権限、byok は利用者のキーかロール
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { AssumeRoleCommand, STSClient } from "@aws-sdk/client-sts";

export type ModelCredential =
  | { kind: "anthropic_api_key"; api_key: string }
  | { kind: "aws_role"; role_arn: string; region: string; external_id: string };

export interface AssumedCredentials {
  accessKeyId: string;
  secretAccessKey: string;
  sessionToken: string;
  expiration?: Date;
}

export type AssumeRole = (cred: Extract<ModelCredential, { kind: "aws_role" }>, sessionName: string) => Promise<AssumedCredentials>;

// 外部 ID を必ず付けて引き受ける（決定 10）
export const stsAssumeRole: AssumeRole = async (cred, sessionName) => {
  const out = await new STSClient({ region: cred.region }).send(
    new AssumeRoleCommand({ RoleArn: cred.role_arn, ExternalId: cred.external_id, RoleSessionName: sessionName.slice(0, 64), DurationSeconds: 3600 }),
  );
  const c = out.Credentials;
  if (!c?.AccessKeyId || !c.SecretAccessKey || !c.SessionToken) throw new Error("ロールを引き受けられません");
  return { accessKeyId: c.AccessKeyId, secretAccessKey: c.SecretAccessKey, sessionToken: c.SessionToken, expiration: c.Expiration };
};

// ロールを引き受けた資格を置くファイルの名前。子の claude は credential_process でこれを読み、期限が近づくと読み直す
export const ROLE_PROFILE = "ndf-byok";
// 引き受け直す間隔。ロールの連鎖は 1 時間で切れるため、切れる前に置き直す
export const ROLE_REFRESH_MS = 30 * 60 * 1000;
const ROLE_FALLBACK_SECONDS = 50 * 60;

// 引き受けた資格を credential_process の形（Version 1）で書く。古い資格は子の SDK が期限の前に読み直す
export async function writeRoleCredentials(
  credential: Extract<ModelCredential, { kind: "aws_role" }>,
  assume: AssumeRole,
  sessionName: string,
  dir: string,
): Promise<{ configFile: string }> {
  const c = await assume(credential, sessionName);
  await mkdir(dir, { recursive: true, mode: 0o700 });
  const credentialsFile = join(dir, "credentials.json");
  const configFile = join(dir, "config");
  const expiration = c.expiration ?? new Date(Date.now() + ROLE_FALLBACK_SECONDS * 1000);
  const body = { Version: 1, AccessKeyId: c.accessKeyId, SecretAccessKey: c.secretAccessKey, SessionToken: c.sessionToken, Expiration: expiration.toISOString() };
  await writeFile(credentialsFile, JSON.stringify(body), { mode: 0o600 });
  await writeFile(configFile, `[profile ${ROLE_PROFILE}]\nregion = ${credential.region}\ncredential_process = cat "${credentialsFile}"\n`, { mode: 0o600 });
  return { configFile };
}

// 子へ渡さない環境変数（秘密の置き場の ARN と、受け口の token。手元のランナーの INGEST_TOKEN も含む）
const PARENT_ONLY = ["INGEST_TOKEN", "INGEST_TOKEN_SECRET_ARN", "MODEL_CREDENTIAL_SECRET_ARN", "ANTHROPIC_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"];

export async function claudeEnv(
  base: Record<string, string | undefined>,
  mode: "local" | "byok" | "managed",
  credential: ModelCredential | null,
  assume: AssumeRole,
  sessionName: string,
  credentialDir: string,
): Promise<Record<string, string>> {
  const env: Record<string, string> = {};
  for (const [k, v] of Object.entries(base)) if (v !== undefined && !PARENT_ONLY.includes(k)) env[k] = v;
  if (mode === "managed") {
    env.CLAUDE_CODE_USE_BEDROCK = "1";
    env.AWS_REGION = base.BEDROCK_REGION ?? base.AWS_REGION ?? "us-east-1";
    return env;
  }
  if (mode === "byok") {
    if (!credential) throw new Error("持ち込んだキーがありません");
    if (credential.kind === "anthropic_api_key") {
      env.ANTHROPIC_API_KEY = credential.api_key;
      return env;
    }
    // 資格を環境変数へ固定すると 1 時間で切れるため、親が置き直すファイルを credential_process で読ませる
    const { configFile } = await writeRoleCredentials(credential, assume, sessionName, credentialDir);
    env.CLAUDE_CODE_USE_BEDROCK = "1";
    env.AWS_REGION = credential.region;
    env.AWS_CONFIG_FILE = configFile;
    env.AWS_PROFILE = ROLE_PROFILE;
    env.AWS_SDK_LOAD_CONFIG = "1";
    // タスクの役割の資格を子が使わないよう、コンテナの資格の口を外す
    delete env.AWS_CONTAINER_CREDENTIALS_RELATIVE_URI;
    delete env.AWS_CONTAINER_CREDENTIALS_FULL_URI;
    return env;
  }
  return env;
}
