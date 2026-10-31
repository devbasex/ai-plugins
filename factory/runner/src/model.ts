// 形態ごとの claude の呼び方（受け入れ条件 29）。アクセスキーをイメージと環境変数に置かず、
// 子の環境にだけ必要な値を置く。managed はタスクの役割の権限、byok は利用者のキーかロール
import { AssumeRoleCommand, STSClient } from "@aws-sdk/client-sts";

export type ModelCredential =
  | { kind: "anthropic_api_key"; api_key: string }
  | { kind: "aws_role"; role_arn: string; region: string; external_id: string };

export interface AssumedCredentials {
  accessKeyId: string;
  secretAccessKey: string;
  sessionToken: string;
}

export type AssumeRole = (cred: Extract<ModelCredential, { kind: "aws_role" }>, sessionName: string) => Promise<AssumedCredentials>;

// 外部 ID を必ず付けて引き受ける（決定 10）
export const stsAssumeRole: AssumeRole = async (cred, sessionName) => {
  const out = await new STSClient({ region: cred.region }).send(
    new AssumeRoleCommand({ RoleArn: cred.role_arn, ExternalId: cred.external_id, RoleSessionName: sessionName.slice(0, 64), DurationSeconds: 3600 }),
  );
  const c = out.Credentials;
  if (!c?.AccessKeyId || !c.SecretAccessKey || !c.SessionToken) throw new Error("ロールを引き受けられません");
  return { accessKeyId: c.AccessKeyId, secretAccessKey: c.SecretAccessKey, sessionToken: c.SessionToken };
};

// 子へ渡さない環境変数（秘密の置き場の ARN と、受け口の token）
const PARENT_ONLY = ["INGEST_TOKEN_SECRET_ARN", "MODEL_CREDENTIAL_SECRET_ARN", "ANTHROPIC_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"];

export async function claudeEnv(
  base: Record<string, string | undefined>,
  mode: "local" | "byok" | "managed",
  credential: ModelCredential | null,
  assume: AssumeRole,
  sessionName: string,
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
    const c = await assume(credential, sessionName);
    env.CLAUDE_CODE_USE_BEDROCK = "1";
    env.AWS_REGION = credential.region;
    env.AWS_ACCESS_KEY_ID = c.accessKeyId;
    env.AWS_SECRET_ACCESS_KEY = c.secretAccessKey;
    env.AWS_SESSION_TOKEN = c.sessionToken;
    // タスクの役割の資格を子が使わないよう、コンテナの資格の口を外す
    delete env.AWS_CONTAINER_CREDENTIALS_RELATIVE_URI;
    delete env.AWS_CONTAINER_CREDENTIALS_FULL_URI;
    return env;
  }
  return env;
}
