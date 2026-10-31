import { describe, expect, it, vi } from "vitest";
import { decodeJwt, exportJWK, generateKeyPair, SignJWT } from "jose";
import { mockClient } from "aws-sdk-client-mock";
import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";
import {
  createCachedRunJobTokenVerifier,
  loadVerifyKeys,
  RunJobTokenError,
  signRunJobToken,
  toRunJobTokenError,
} from "./run-job-token.ts";
import { createRunJobTokenKit, TEST_RUN_JOB_KID } from "../testing/run-job-tokens.ts";

const claims = { jobId: "job-1", sprintRunId: "sr-1", repo: "org-a/repo-a" };

async function codeOf(p: Promise<unknown>): Promise<string> {
  try {
    await p;
  } catch (err) {
    expect(err).toBeInstanceOf(RunJobTokenError);
    return (err as RunJobTokenError).code;
  }
  throw new Error("拒まれなかった");
}

describe("実行ジョブの token の検証（受け入れ条件 17・18、決定 5）", () => {
  it("正しい token から実行ジョブの識別子と sprint_run_id を返す", async () => {
    const kit = await createRunJobTokenKit();
    const expiresAt = new Date(Math.floor(Date.now() / 1000 + 600) * 1000);
    expect(await kit.verify(await kit.sign({ ...claims, expiresAt }))).toEqual({ jobId: "job-1", sprintRunId: "sr-1", repo: "org-a/repo-a", expiresAt });
  });

  it("形が違う token は malformed", async () => {
    const kit = await createRunJobTokenKit();
    expect(await codeOf(kit.verify("not-a-token"))).toBe("malformed");
    expect(await codeOf(kit.verify("a.b.c"))).toBe("malformed");
  });

  it("署名が合わない token は bad_signature", async () => {
    const kit = await createRunJobTokenKit();
    expect(await codeOf(kit.verify(await kit.otherKeySign(claims)))).toBe("bad_signature");
  });

  it("期限が切れた token は expired", async () => {
    const kit = await createRunJobTokenKit();
    expect(await codeOf(kit.verify(await kit.sign({ ...claims, expiresAt: new Date(Date.now() - 60_000) })))).toBe("expired");
  });

  it("知らない鍵（kid）で作られた token は unknown_key", async () => {
    const kit = await createRunJobTokenKit();
    expect(await codeOf(kit.verify(await kit.sign(claims, { kid: "retired-kid" })))).toBe("unknown_key");
  });

  it("発行者・宛先・typ・sprint_run_id が違う token は malformed", async () => {
    const kit = await createRunJobTokenKit();
    const { privateKey, publicKey } = await generateKeyPair("EdDSA", { crv: "Ed25519" });
    const jwk = { ...(await exportJWK(publicKey)), kid: "k", alg: "EdDSA" };
    const { createRunJobTokenVerifier } = await import("./run-job-token.ts");
    const verify = createRunJobTokenVerifier([jwk]);
    const base = () =>
      new SignJWT({ sprint_run_id: "sr-1", repo: "org-a/repo-a" })
        .setIssuer("ndf-factory/run-control")
        .setAudience("ndf-factory/run-job")
        .setSubject("job-1")
        .setExpirationTime("1h");
    const header = { alg: "EdDSA", typ: "ndf-run-job+jwt", kid: "k" };
    expect(await verify(await base().setProtectedHeader(header).sign(privateKey))).toMatchObject({ jobId: "job-1" });
    expect(await codeOf(verify(await base().setIssuer("other").setProtectedHeader(header).sign(privateKey)))).toBe("malformed");
    expect(await codeOf(verify(await base().setAudience("other").setProtectedHeader(header).sign(privateKey)))).toBe("malformed");
    expect(await codeOf(verify(await base().setProtectedHeader({ ...header, typ: "JWT" }).sign(privateKey)))).toBe("malformed");
    const noRun = new SignJWT({}).setIssuer("ndf-factory/run-control").setAudience("ndf-factory/run-job").setSubject("job-1").setExpirationTime("1h");
    expect(await codeOf(verify(await noRun.setProtectedHeader(header).sign(privateKey)))).toBe("malformed");
    expect(await codeOf(verify(await base().setSubject("job#1").setProtectedHeader(header).sign(privateKey)))).toBe("malformed");
    expect(kit.publicJwks.keys[0]!.kid).toBe(TEST_RUN_JOB_KID);
  });

  it("jose の失敗の code を 4 つの「無効」へ読み替え、知らない code は読み替えない", () => {
    const table: [string, string][] = [
      ["ERR_JWS_INVALID", "malformed"],
      ["ERR_JWT_INVALID", "malformed"],
      ["ERR_JWT_CLAIM_VALIDATION_FAILED", "malformed"],
      ["ERR_JOSE_ALG_NOT_ALLOWED", "malformed"],
      ["ERR_JWS_SIGNATURE_VERIFICATION_FAILED", "bad_signature"],
      ["ERR_JWT_EXPIRED", "expired"],
      ["ERR_JWKS_NO_MATCHING_KEY", "unknown_key"],
    ];
    for (const [code, expected] of table) expect(toRunJobTokenError({ code })?.code).toBe(expected);
    expect(toRunJobTokenError(new Error("x"))).toBeNull();
  });

  it("失敗の文に token の値を含めない", async () => {
    const kit = await createRunJobTokenKit();
    const token = await kit.otherKeySign(claims);
    await kit.verify(token).catch((err: Error) => expect(err.message).not.toContain(token));
  });
});

describe("実行ジョブの token の発行（受け入れ条件 19、I31）", () => {
  it("account_id を claims に渡すと型の検査で落ち、渡しても token に載らない", async () => {
    const { privateKey } = await generateKeyPair("EdDSA", { crv: "Ed25519" });
    const expiresAt = new Date(Date.now() + 60_000);
    const token = await signRunJobToken(
      // @ts-expect-error account_id は token に載せない（#2170 の約束）
      { jobId: "job-1", sprintRunId: "sr-1", repo: "org-a/repo-a", expiresAt, account_id: "acc-1" },
      { kid: "k", privateKey },
    );
    expect(Object.keys(decodeJwt(token)).sort()).toEqual(["aud", "exp", "iat", "iss", "repo", "sprint_run_id", "sub"]);
  });

  it("repo を token に載せ、検証で返す。repo の無い token は malformed（I37）", async () => {
    const kit = await createRunJobTokenKit();
    const token = await kit.sign({ jobId: "job-1", sprintRunId: "sr-1", repo: "Org-A/Repo-A" });
    expect((await kit.verify(token)).repo).toBe("Org-A/Repo-A");
    const { privateKey } = await generateKeyPair("EdDSA", { crv: "Ed25519" });
    const noRepo = await new SignJWT({ sprint_run_id: "sr-1" })
      .setProtectedHeader({ alg: "EdDSA", typ: "ndf-run-job+jwt", kid: "k3" })
      .setIssuer("ndf-factory/run-control")
      .setAudience("ndf-factory/run-job")
      .setSubject("job-1")
      .setExpirationTime("1h")
      .sign(privateKey);
    const { createRunJobTokenVerifier } = await import("./run-job-token.ts");
    const pub = { ...(await exportJWK((await generateKeyPair("EdDSA", { crv: "Ed25519" })).publicKey)), kid: "k3" };
    await expect(createRunJobTokenVerifier([pub])(noRepo)).rejects.toBeInstanceOf(RunJobTokenError);
  });

  it("Ed25519 でない鍵では作らない", async () => {
    const rsa = await generateKeyPair("RS256", { extractable: true });
    const expiresAt = new Date(Date.now() + 60_000);
    await expect(signRunJobToken({ ...claims, expiresAt }, { kid: "k", privateKey: rsa.privateKey })).rejects.toThrow("Ed25519");
    await expect(signRunJobToken({ ...claims, expiresAt }, { kid: "k", privateKey: await exportJWK(rsa.privateKey) })).rejects.toThrow("Ed25519");
  });

  it("秘密鍵の JWK からも作れ、公開鍵の集合で確かめられる", async () => {
    const kit = await createRunJobTokenKit();
    const pair = await generateKeyPair("EdDSA", { crv: "Ed25519", extractable: true });
    const jwk = await exportJWK(pair.privateKey);
    const pub = { ...(await exportJWK(pair.publicKey)), kid: "k2" };
    const { createRunJobTokenVerifier } = await import("./run-job-token.ts");
    const token = await signRunJobToken({ ...claims, expiresAt: new Date(Date.now() + 60_000) }, { kid: "k2", privateKey: jwk });
    expect((await createRunJobTokenVerifier([...kit.publicJwks.keys, pub])(token)).jobId).toBe("job-1");
  });
});

describe("公開鍵の集合の読み出し", () => {
  it("Secrets Manager の {keys} を読み、5 分ごとに読み直す", async () => {
    const kit = await createRunJobTokenKit();
    const sm = mockClient(SecretsManagerClient);
    sm.on(GetSecretValueCommand).resolves({ SecretString: JSON.stringify(kit.publicJwks) });
    let t = 0;
    const load = vi.fn(() => loadVerifyKeys("arn:aws:secretsmanager:us-east-1:1:secret:k", new SecretsManagerClient({ region: "us-east-1" })));
    const verify = createCachedRunJobTokenVerifier(load, 300_000, () => t);
    const token = await kit.sign(claims);
    await verify(token);
    t = 299_999;
    await verify(token);
    expect(load).toHaveBeenCalledTimes(1);
    t = 300_000;
    await verify(token);
    expect(load).toHaveBeenCalledTimes(2);
    sm.restore();
  });

  it("読めない失敗は RunJobTokenError でない例外にする（呼ぶ側が 503 にする）", async () => {
    const verify = createCachedRunJobTokenVerifier(() => Promise.reject(new Error("AccessDenied")));
    await expect(verify("x")).rejects.not.toBeInstanceOf(RunJobTokenError);
  });
});
