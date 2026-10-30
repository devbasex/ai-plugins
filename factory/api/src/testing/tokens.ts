// テストの中で作った鍵で Cognito の access token を模す。鍵はファイルに置かない（境界の検査、受け入れ条件 14）
import { exportJWK, generateKeyPair, SignJWT, type CryptoKey, type JWK } from "jose";
import { createAccessTokenVerifier, type AccessTokenVerifier } from "../auth/verify.ts";

export const POOL_ID = "us-east-1_TestPool1";
export const OTHER_POOL_ID = "us-east-1_OtherPool";
export const CLIENT_ID = "test-client-id";
export const ISSUER = `https://cognito-idp.us-east-1.amazonaws.com/${POOL_ID}`;

export interface TokenKit {
  verifier: AccessTokenVerifier;
  sign(claims?: Record<string, unknown>, opts?: { key?: "main" | "other"; expiresIn?: string | number }): Promise<string>;
  bearer(userId: string, idp?: "Google" | "GitHub"): Promise<string>;
}

export async function createTokenKit(): Promise<TokenKit> {
  const main = await generateKeyPair("RS256");
  const other = await generateKeyPair("RS256");
  const jwk: JWK = { ...(await exportJWK(main.publicKey)), kid: "test-kid", alg: "RS256", use: "sig" };
  const verifier = createAccessTokenVerifier({ userPoolId: POOL_ID, clientId: CLIENT_ID });
  verifier.cacheJwks({ keys: [jwk as never] });

  async function sign(claims: Record<string, unknown> = {}, opts: { key?: "main" | "other"; expiresIn?: string | number } = {}) {
    const key: CryptoKey = (opts.key === "other" ? other : main).privateKey;
    return new SignJWT({ token_use: "access", client_id: CLIENT_ID, iss: ISSUER, ...claims })
      .setProtectedHeader({ alg: "RS256", kid: "test-kid" })
      .setIssuedAt()
      .setExpirationTime(opts.expiresIn ?? "1h")
      .sign(key);
  }

  return {
    verifier,
    sign,
    async bearer(userId, idp = "Google") {
      return `Bearer ${await sign({ sub: userId, username: `${idp}_${userId}` })}`;
    },
  };
}
