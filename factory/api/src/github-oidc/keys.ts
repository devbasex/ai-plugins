import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";
import { calculateJwkThumbprint, exportJWK, importJWK, importPKCS8, type CryptoKey, type JWK } from "jose";

// GitHub の仲立ちが ID token と、包んだ state・code を署名する鍵（決定 3）
export interface SigningKeys {
  privateKey: CryptoKey;
  publicKey: CryptoKey;
  publicJwk: JWK & { kid: string };
}

const PRIVATE_MEMBERS = ["d", "p", "q", "dp", "dq", "qi"] as const;

export async function signingKeysFromPrivateKey(privateKey: CryptoKey): Promise<SigningKeys> {
  const jwk = await exportJWK(privateKey);
  for (const m of PRIVATE_MEMBERS) delete jwk[m];
  const kid = await calculateJwkThumbprint(jwk);
  const publicJwk = { ...jwk, kid, alg: "RS256", use: "sig" };
  const publicKey = (await importJWK(publicJwk, "RS256")) as CryptoKey;
  return { privateKey, publicKey, publicJwk };
}

// PKCS#8 の PEM の秘密鍵から鍵を作る
export async function signingKeysFromPem(pem: string): Promise<SigningKeys> {
  return signingKeysFromPrivateKey(await importPKCS8(pem, "RS256", { extractable: true }));
}

// Secrets Manager の ARN から秘密鍵を読む。値をログと応答に出さない
export async function loadSigningKeys(secretArn: string, client = new SecretsManagerClient({})): Promise<SigningKeys> {
  const out = await client.send(new GetSecretValueCommand({ SecretId: secretArn }));
  if (!out.SecretString) throw new Error("署名の鍵が読めません");
  return signingKeysFromPem(out.SecretString);
}
