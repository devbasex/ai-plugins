// 実行ジョブの token（決定 5）。EdDSA（Ed25519）の JWT で、確かめる側は公開鍵の集合だけを読む。
// account_id は載せない（I31）。token の値と鍵はログ・応答・例外の文に出さない
import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";
import { createLocalJWKSet, importJWK, jwtVerify, SignJWT, type CryptoKey, type JWK } from "jose";

export const RUN_JOB_TOKEN_ISSUER = "ndf-factory/run-control";
export const RUN_JOB_TOKEN_AUDIENCE = "ndf-factory/run-job";
export const RUN_JOB_TOKEN_TYPE = "ndf-run-job+jwt";
export const RUN_JOB_TOKEN_ALG = "EdDSA";

// 公開鍵の集合を読み直す間隔
export const VERIFY_KEYS_TTL_MS = 5 * 60 * 1000;

const IDENTIFIER = /^[A-Za-z0-9_-]{1,128}$/;
// 許可したリポジトリ（owner/name）。契約の RepoFullName と同じ形
const REPO = /^[A-Za-z0-9-]+\/[A-Za-z0-9._-]+$/;

// 発行で受けるのはこの 4 つだけ。ほかの鍵（account_id など）は型で受けない。
// repo はその実行に許可したリポジトリ（#2216。受け口が step.recorded の Pull Request と照らす）
export interface RunJobTokenClaims {
  jobId: string;
  sprintRunId: string;
  repo: string;
  expiresAt: Date;
}

export type VerifiedRunJob = RunJobTokenClaims;

// 発行の秘密鍵と、公開鍵の集合で照らす kid
export interface RunJobSigningKey {
  kid: string;
  privateKey: CryptoKey | JWK;
}

export type RunJobTokenErrorCode = "malformed" | "bad_signature" | "expired" | "unknown_key";

// token が無効。呼ぶ側が 401 にする
export class RunJobTokenError extends Error {
  constructor(readonly code: RunJobTokenErrorCode) {
    super(`実行ジョブの token が無効です（${code}）`);
    this.name = "RunJobTokenError";
  }
}

export type RunJobTokenVerifier = (token: string) => Promise<VerifiedRunJob>;

// jose の失敗の code を 4 つの「無効」へ読み替える
const JOSE_CODES: Record<string, RunJobTokenErrorCode> = {
  ERR_JWS_INVALID: "malformed",
  ERR_JWT_INVALID: "malformed",
  ERR_JWT_CLAIM_VALIDATION_FAILED: "malformed",
  ERR_JOSE_ALG_NOT_ALLOWED: "malformed",
  ERR_JOSE_NOT_SUPPORTED: "malformed",
  ERR_JWS_SIGNATURE_VERIFICATION_FAILED: "bad_signature",
  ERR_JWT_EXPIRED: "expired",
  ERR_JWKS_NO_MATCHING_KEY: "unknown_key",
  ERR_JWKS_MULTIPLE_MATCHING_KEYS: "unknown_key",
};

export function toRunJobTokenError(err: unknown): RunJobTokenError | null {
  const code = (err as { code?: unknown } | null)?.code;
  if (typeof code !== "string") return null;
  const mapped = JOSE_CODES[code];
  return mapped ? new RunJobTokenError(mapped) : null;
}

function isEd25519Jwk(jwk: JWK): boolean {
  return jwk.kty === "OKP" && jwk.crv === "Ed25519";
}

// 公開鍵の JWK の並びから検証の関数を作る
export function createRunJobTokenVerifier(keys: JWK[]): RunJobTokenVerifier {
  const jwks = createLocalJWKSet({ keys: keys.filter(isEd25519Jwk).map((k) => ({ ...k, alg: RUN_JOB_TOKEN_ALG })) });
  return async (token) => {
    let payload;
    try {
      ({ payload } = await jwtVerify(token, jwks, {
        algorithms: [RUN_JOB_TOKEN_ALG],
        issuer: RUN_JOB_TOKEN_ISSUER,
        audience: RUN_JOB_TOKEN_AUDIENCE,
        typ: RUN_JOB_TOKEN_TYPE,
        requiredClaims: ["sub", "exp", "sprint_run_id", "repo"],
      }));
    } catch (err) {
      throw toRunJobTokenError(err) ?? new RunJobTokenError("malformed");
    }
    const { sub, sprint_run_id: sprintRunId, repo, exp } = payload;
    if (typeof sub !== "string" || !IDENTIFIER.test(sub)) throw new RunJobTokenError("malformed");
    if (typeof sprintRunId !== "string" || !IDENTIFIER.test(sprintRunId)) throw new RunJobTokenError("malformed");
    if (typeof repo !== "string" || !REPO.test(repo)) throw new RunJobTokenError("malformed");
    return { jobId: sub, sprintRunId, repo, expiresAt: new Date((exp as number) * 1000) };
  };
}

// 実行ジョブの token を発行の形で作る（#2186 が呼ぶ。期限の長さと鍵の読み出しは #2186 が決める）
export async function signRunJobToken(claims: RunJobTokenClaims, key: RunJobSigningKey): Promise<string> {
  const privateKey =
    "kty" in key.privateKey ? await importJWK(assertEd25519(key.privateKey), RUN_JOB_TOKEN_ALG) : key.privateKey;
  if ("algorithm" in privateKey && privateKey.algorithm.name !== "Ed25519") {
    throw new Error("実行ジョブの token の鍵は Ed25519 にしてください");
  }
  if (!REPO.test(claims.repo)) throw new Error("実行ジョブの token の repo は owner/name の形にしてください");
  return new SignJWT({ sprint_run_id: claims.sprintRunId, repo: claims.repo })
    .setProtectedHeader({ alg: RUN_JOB_TOKEN_ALG, typ: RUN_JOB_TOKEN_TYPE, kid: key.kid })
    .setIssuer(RUN_JOB_TOKEN_ISSUER)
    .setAudience(RUN_JOB_TOKEN_AUDIENCE)
    .setSubject(claims.jobId)
    .setIssuedAt()
    .setExpirationTime(claims.expiresAt)
    .sign(privateKey);
}

function assertEd25519(jwk: JWK): JWK {
  if (!isEd25519Jwk(jwk)) throw new Error("実行ジョブの token の鍵は Ed25519 にしてください");
  return jwk;
}

// Secrets Manager の公開鍵の集合（{"keys": [JWK, …]}）を読む。値をログと応答に出さない
export async function loadVerifyKeys(secretArn: string, client = new SecretsManagerClient({})): Promise<JWK[]> {
  const out = await client.send(new GetSecretValueCommand({ SecretId: secretArn }));
  const parsed = out.SecretString ? (JSON.parse(out.SecretString) as { keys?: unknown }) : null;
  if (!parsed || !Array.isArray(parsed.keys)) throw new Error("実行ジョブの token の公開鍵の集合が読めません");
  return parsed.keys as JWK[];
}

// 公開鍵の集合を ttl ごとに読み直す検証の関数。読めない失敗は RunJobTokenError でない例外（呼ぶ側が 503 にする）
export function createCachedRunJobTokenVerifier(
  loadKeys: () => Promise<JWK[]>,
  ttlMs = VERIFY_KEYS_TTL_MS,
  now: () => number = Date.now,
): RunJobTokenVerifier {
  let cached: { verifier: RunJobTokenVerifier; loadedAt: number } | null = null;
  return async (token) => {
    if (!cached || now() - cached.loadedAt >= ttlMs) {
      cached = { verifier: createRunJobTokenVerifier(await loadKeys()), loadedAt: now() };
    }
    return cached.verifier(token);
  };
}

let envVerifier: RunJobTokenVerifier | null = null;

// RUN_JOB_TOKEN_VERIFY_KEYS_SECRET_ARN の公開鍵の集合で確かめる（受け口と #2173 が呼ぶ）
export function runJobTokenVerifierFromEnv(): RunJobTokenVerifier {
  envVerifier ??= createCachedRunJobTokenVerifier(() => {
    const arn = process.env.RUN_JOB_TOKEN_VERIFY_KEYS_SECRET_ARN;
    if (!arn) throw new Error("環境変数 RUN_JOB_TOKEN_VERIFY_KEYS_SECRET_ARN がありません");
    return loadVerifyKeys(arn);
  });
  return envVerifier;
}
