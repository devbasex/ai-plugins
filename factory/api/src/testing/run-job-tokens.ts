// 省いたときの許可したリポジトリ
export const TEST_RUN_JOB_REPO = "owner/name";

type KitClaims = Omit<RunJobTokenClaims, "expiresAt" | "repo"> & { expiresAt?: Date; repo?: string };

// テストの中で作った鍵で実行ジョブの token を作る。鍵はメモリの中だけに置き、ファイルに置かない（境界の検査）
import { exportJWK, generateKeyPair, type JWK } from "jose";
import {
  createRunJobTokenVerifier,
  signRunJobToken,
  type RunJobSigningKey,
  type RunJobTokenClaims,
  type RunJobTokenVerifier,
} from "../progress/run-job-token.ts";

export interface RunJobTokenKit {
  // 検証の関数が知っている鍵で作る。kid を変えると「知らない鍵」の token になる
  sign(claims: KitClaims, opts?: { kid?: string }): Promise<string>;
  // 公開鍵の集合に無い別の鍵で、同じ kid のまま作る（署名が合わない token）
  otherKeySign(claims: KitClaims): Promise<string>;
  verify: RunJobTokenVerifier;
  publicJwks: { keys: JWK[] };
  // 検証の関数が知っている発行の鍵（実行の制御のテストが使う）
  signingKey: RunJobSigningKey;
}

export const TEST_RUN_JOB_KID = "test-run-job-kid";

export async function createRunJobTokenKit(): Promise<RunJobTokenKit> {
  const main = await generateKeyPair("EdDSA", { crv: "Ed25519" });
  const other = await generateKeyPair("EdDSA", { crv: "Ed25519" });
  const publicJwk: JWK = { ...(await exportJWK(main.publicKey)), kid: TEST_RUN_JOB_KID, alg: "EdDSA", use: "sig" };
  const publicJwks = { keys: [publicJwk] };
  const withExpiry = (claims: KitClaims): RunJobTokenClaims => ({
    jobId: claims.jobId,
    sprintRunId: claims.sprintRunId,
    repo: claims.repo ?? TEST_RUN_JOB_REPO,
    expiresAt: claims.expiresAt ?? new Date(Date.now() + 60 * 60 * 1000),
  });
  return {
    sign: (claims, opts = {}) =>
      signRunJobToken(withExpiry(claims), { kid: opts.kid ?? TEST_RUN_JOB_KID, privateKey: main.privateKey }),
    otherKeySign: (claims) => signRunJobToken(withExpiry(claims), { kid: TEST_RUN_JOB_KID, privateKey: other.privateKey }),
    verify: createRunJobTokenVerifier(publicJwks.keys),
    publicJwks,
    signingKey: { kid: TEST_RUN_JOB_KID, privateKey: main.privateKey },
  };
}
