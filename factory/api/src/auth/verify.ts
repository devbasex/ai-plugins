import { CognitoJwtVerifier } from "aws-jwt-verify";
import { JwtBaseError } from "aws-jwt-verify/error";
import { AuthError } from "../errors.ts";
import type { Caller, Idp } from "../accounts/model.ts";

export interface VerifierConfig {
  userPoolId: string;
  clientId: string;
}

// Cognito の IdP の名前（private リポジトリが登録する）と、token の username の頭
const IDP_PREFIXES: ReadonlyArray<[string, Idp]> = [
  ["Google_", "google"],
  ["GitHub_", "github"],
];

// token の username の頭から IdP を決める。文字列でない・知らない頭なら undefined
function idpFromUsername(username: unknown): Idp | undefined {
  const name = typeof username === "string" ? username : "";
  return IDP_PREFIXES.find(([prefix]) => name.startsWith(prefix))?.[1];
}

export type AccessTokenVerifier = ReturnType<typeof createAccessTokenVerifier>;

export function createAccessTokenVerifier(config: VerifierConfig) {
  return CognitoJwtVerifier.create({
    userPoolId: config.userPoolId,
    clientId: config.clientId,
    tokenUse: "access",
  });
}

// Authorization の値を、署名・発行元・クライアント・期限・token_use で検証し、利用者を返す（決定 9）
// どの失敗も 401 にし、理由を応答に出さない
export async function verifyAuthorization(verifier: AccessTokenVerifier, authorization: string | undefined | null): Promise<Caller> {
  const match = /^Bearer ([^\s]+)$/.exec(authorization ?? "");
  if (!match) throw new AuthError();
  let payload: Awaited<ReturnType<AccessTokenVerifier["verify"]>>;
  try {
    payload = await verifier.verify(match[1]!);
  } catch (err) {
    if (err instanceof JwtBaseError) throw new AuthError();
    throw err;
  }
  const idp = idpFromUsername(payload.username);
  if (!idp || typeof payload.sub !== "string" || payload.sub === "") throw new AuthError();
  return { userId: payload.sub, idp };
}
