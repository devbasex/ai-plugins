import { Hono, type Context } from "hono";
import { jwtVerify, SignJWT } from "jose";
import type { SigningKeys } from "./keys.ts";

// GitHub の OAuth を、Cognito が OIDC の IdP として受け取れる形へ変える仲立ち（決定 3）。
// 経路と照合は issues/issue-2170-design-interfaces.md の「GitHub の仲立ち」
export interface GithubOidcConfig {
  // 仲立ちの発行元（GITHUB_OIDC_ISSUER）。経路はこの URL の path の下に置く
  issuer: string;
  // Cognito の管理されたログインのドメイン（COGNITO_DOMAIN）
  cognitoDomain: string;
  // GitHub の OAuth App のクライアントの ID（GITHUB_OAUTH_CLIENT_ID）
  githubClientId: string;
}

export interface GithubOidcDeps {
  keys: () => Promise<SigningKeys>;
  fetch?: typeof fetch;
}

export const GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize";
export const GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token";
export const GITHUB_API_URL = "https://api.github.com";

const STATE_TYP = "ndf-github-state+jwt";
const CODE_TYP = "ndf-github-code+jwt";
const STATE_TTL = "10m";
const CODE_TTL = "5m";
const ID_TOKEN_TTL_SECONDS = 300;

interface Wrapped {
  client_id: string;
  redirect_uri: string;
  nonce?: string;
}

interface WrappedState extends Wrapped {
  state?: string;
}

interface WrappedCode extends Wrapped {
  gh_code: string;
}

interface GithubUser {
  id: number;
  login: string;
  name: string | null;
}

interface GithubEmail {
  email: string;
  primary: boolean;
  verified: boolean;
}

function oauthError(c: Context, error: string, status: 400 | 401 = 400) {
  return c.json({ error }, status);
}

export function createGithubOidcApp(config: GithubOidcConfig, deps: GithubOidcDeps): Hono {
  const issuer = config.issuer.replace(/\/$/, "");
  const cognitoRedirect = `https://${config.cognitoDomain}/oauth2/idpresponse`;
  const callbackUrl = `${issuer}/callback`;
  const doFetch = deps.fetch ?? fetch;
  const app = new Hono().basePath(new URL(issuer).pathname.replace(/\/$/, "") || "/");

  async function wrap(typ: string, ttl: string, claims: object): Promise<string> {
    const keys = await deps.keys();
    return new SignJWT({ ...claims })
      .setProtectedHeader({ alg: "RS256", kid: keys.publicJwk.kid, typ })
      .setIssuer(issuer)
      .setAudience(issuer)
      .setIssuedAt()
      .setExpirationTime(ttl)
      .sign(keys.privateKey);
  }

  async function unwrap<T>(typ: string, token: string | undefined): Promise<T | null> {
    if (!token) return null;
    try {
      const { payload } = await jwtVerify(token, (await deps.keys()).publicKey, {
        issuer,
        audience: issuer,
        typ,
        algorithms: ["RS256"],
      });
      return payload as T;
    } catch {
      return null;
    }
  }

  async function githubJson<T>(path: string, accessToken: string): Promise<T | null> {
    const res = await doFetch(`${GITHUB_API_URL}${path}`, {
      headers: {
        Authorization: `Bearer ${accessToken}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "ndf-factory",
      },
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  }

  app.get("/.well-known/openid-configuration", (c) =>
    c.json({
      issuer,
      authorization_endpoint: `${issuer}/authorize`,
      token_endpoint: `${issuer}/token`,
      userinfo_endpoint: `${issuer}/userinfo`,
      jwks_uri: `${issuer}/jwks.json`,
      response_types_supported: ["code"],
      subject_types_supported: ["public"],
      id_token_signing_alg_values_supported: ["RS256"],
      scopes_supported: ["openid", "profile", "email"],
      token_endpoint_auth_methods_supported: ["client_secret_post", "client_secret_basic"],
    }),
  );

  app.get("/jwks.json", async (c) => c.json({ keys: [(await deps.keys()).publicJwk] }));

  // Cognito の戻りの経路と設定のクライアントにだけ code を送る（オープンリダイレクトにしない）
  app.get("/authorize", async (c) => {
    const q = c.req.query();
    if (q.redirect_uri !== cognitoRedirect || q.client_id !== config.githubClientId) {
      return oauthError(c, "invalid_request");
    }
    const wrapped: WrappedState = { client_id: q.client_id, redirect_uri: q.redirect_uri, state: q.state, nonce: q.nonce };
    const url = new URL(GITHUB_AUTHORIZE_URL);
    url.searchParams.set("client_id", config.githubClientId);
    url.searchParams.set("redirect_uri", callbackUrl);
    url.searchParams.set("scope", "read:user user:email");
    url.searchParams.set("state", await wrap(STATE_TYP, STATE_TTL, wrapped));
    return c.redirect(url.toString(), 302);
  });

  app.get("/callback", async (c) => {
    const q = c.req.query();
    const st = await unwrap<WrappedState>(STATE_TYP, q.state);
    if (!st || st.redirect_uri !== cognitoRedirect || st.client_id !== config.githubClientId) {
      return oauthError(c, "invalid_request");
    }
    const back = new URL(st.redirect_uri);
    if (st.state !== undefined) back.searchParams.set("state", st.state);
    if (q.error || !q.code) {
      back.searchParams.set("error", q.error ?? "access_denied");
      return c.redirect(back.toString(), 302);
    }
    const code: WrappedCode = { gh_code: q.code, client_id: st.client_id, redirect_uri: st.redirect_uri, nonce: st.nonce };
    back.searchParams.set("code", await wrap(CODE_TYP, CODE_TTL, code));
    return c.redirect(back.toString(), 302);
  });

  app.post("/token", async (c) => {
    const form = await c.req.parseBody();
    const text = (k: string) => (typeof form[k] === "string" ? (form[k] as string) : undefined);
    let clientId = text("client_id");
    let clientSecret = text("client_secret");
    const basic = /^Basic (.+)$/.exec(c.req.header("Authorization") ?? "");
    if (basic) {
      const [id, secret] = Buffer.from(basic[1]!, "base64").toString("utf8").split(":");
      clientId = decodeURIComponent(id ?? "");
      clientSecret = decodeURIComponent(secret ?? "");
    }
    if (text("grant_type") !== "authorization_code" || !clientId || !clientSecret) {
      return oauthError(c, "invalid_request");
    }
    const code = await unwrap<WrappedCode>(CODE_TYP, text("code"));
    if (!code || code.client_id !== clientId || code.redirect_uri !== text("redirect_uri")) {
      return oauthError(c, "invalid_grant");
    }
    const tokenRes = await doFetch(GITHUB_TOKEN_URL, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: clientId,
        client_secret: clientSecret,
        code: code.gh_code,
        redirect_uri: callbackUrl,
      }),
    });
    const token = tokenRes.ok ? ((await tokenRes.json()) as { access_token?: string; scope?: string }) : {};
    if (!token.access_token) return oauthError(c, "invalid_grant");
    const user = await githubJson<GithubUser>("/user", token.access_token);
    if (!user || typeof user.id !== "number") return oauthError(c, "invalid_grant");
    const keys = await deps.keys();
    // sub は GitHub の数の利用者 ID。利用者が変えられるログイン名とメールを使わない
    const idToken = await new SignJWT(code.nonce === undefined ? {} : { nonce: code.nonce })
      .setProtectedHeader({ alg: "RS256", kid: keys.publicJwk.kid, typ: "JWT" })
      .setIssuer(issuer)
      .setSubject(String(user.id))
      .setAudience(clientId)
      .setIssuedAt()
      .setExpirationTime(`${ID_TOKEN_TTL_SECONDS}s`)
      .sign(keys.privateKey);
    return c.json({
      access_token: token.access_token,
      token_type: "Bearer",
      id_token: idToken,
      scope: token.scope ?? "",
    });
  });

  app.on(["GET", "POST"], "/userinfo", async (c) => {
    const bearer = /^Bearer (.+)$/.exec(c.req.header("Authorization") ?? "");
    if (!bearer) return oauthError(c, "invalid_token", 401);
    const user = await githubJson<GithubUser>("/user", bearer[1]!);
    if (!user || typeof user.id !== "number") return oauthError(c, "invalid_token", 401);
    const emails = (await githubJson<GithubEmail[]>("/user/emails", bearer[1]!)) ?? [];
    const primary = emails.find((e) => e.primary && e.verified);
    return c.json({
      sub: String(user.id),
      name: user.name ?? user.login,
      preferred_username: user.login,
      ...(primary ? { email: primary.email, email_verified: true } : {}),
    });
  });

  return app;
}
