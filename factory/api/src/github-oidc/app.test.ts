import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import { createLocalJWKSet, decodeJwt, generateKeyPair, jwtVerify, SignJWT, type JWK } from "jose";
import { createGithubOidcApp, GITHUB_API_URL, GITHUB_TOKEN_URL } from "./app.ts";
import { signingKeysFromPrivateKey, type SigningKeys } from "./keys.ts";

const ISSUER = "https://api.example.test/oidc/github";
const COGNITO_DOMAIN = "auth.example.test";
const COGNITO_REDIRECT = `https://${COGNITO_DOMAIN}/oauth2/idpresponse`;
const GH_CLIENT = "gh-client";
const GH_SECRET = "gh-secret-value";

let keys: SigningKeys;
let otherKeys: SigningKeys;
beforeAll(async () => {
  keys = await signingKeysFromPrivateKey((await generateKeyPair("RS256", { extractable: true })).privateKey);
  otherKeys = await signingKeysFromPrivateKey((await generateKeyPair("RS256", { extractable: true })).privateKey);
});

interface GithubFake {
  requests: { url: string; body?: string }[];
  emails: { email: string; primary: boolean; verified: boolean }[];
}

let gh: GithubFake;
let app: ReturnType<typeof createGithubOidcApp>;
beforeEach(() => {
  gh = { requests: [], emails: [{ email: "me@example.test", primary: true, verified: true }] };
  const fakeFetch: typeof fetch = async (input, init) => {
    const url = String(input);
    gh.requests.push({ url, body: init?.body?.toString() });
    if (url === GITHUB_TOKEN_URL) {
      const body = new URLSearchParams(init?.body?.toString());
      if (body.get("code") !== "gh-code" || body.get("client_secret") !== GH_SECRET) return Response.json({ error: "bad_verification_code" });
      return Response.json({ access_token: "gho_token", token_type: "bearer", scope: "read:user,user:email" });
    }
    const auth = new Headers(init?.headers).get("Authorization");
    if (auth !== "Bearer gho_token") return new Response("", { status: 401 });
    if (url === `${GITHUB_API_URL}/user`) return Response.json({ id: 4242, login: "octo", name: null });
    if (url === `${GITHUB_API_URL}/user/emails`) return Response.json(gh.emails);
    return new Response("", { status: 404 });
  };
  app = createGithubOidcApp({ issuer: ISSUER, cognitoDomain: COGNITO_DOMAIN, githubClientId: GH_CLIENT }, { keys: async () => keys, fetch: fakeFetch });
});

const path = (p: string) => `/oidc/github${p}`;

async function authorize(params: Record<string, string> = {}) {
  const q = new URLSearchParams({
    client_id: GH_CLIENT,
    redirect_uri: COGNITO_REDIRECT,
    response_type: "code",
    scope: "openid",
    state: "cognito-state",
    nonce: "cognito-nonce",
    ...params,
  });
  return app.request(path(`/authorize?${q}`));
}

async function callback(state: string, extra: Record<string, string> = { code: "gh-code" }) {
  return app.request(path(`/callback?${new URLSearchParams({ state, ...extra })}`));
}

async function token(code: string, form: Record<string, string> = {}) {
  return app.request(path("/token"), {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: COGNITO_REDIRECT,
      client_id: GH_CLIENT,
      client_secret: GH_SECRET,
      ...form,
    }),
  });
}

async function fullCode(params: Record<string, string> = {}) {
  const loc = new URL((await authorize(params)).headers.get("Location")!);
  const back = new URL((await callback(loc.searchParams.get("state")!)).headers.get("Location")!);
  return back;
}

describe("GitHub の仲立ち（決定 3）", () => {
  it("発見の文書と公開鍵を返す", async () => {
    const conf = (await (await app.request(path("/.well-known/openid-configuration"))).json()) as Record<string, unknown>;
    expect(conf).toMatchObject({ issuer: ISSUER, token_endpoint: `${ISSUER}/token`, jwks_uri: `${ISSUER}/jwks.json`, id_token_signing_alg_values_supported: ["RS256"] });
    const jwks = (await (await app.request(path("/jwks.json"))).json()) as { keys: JWK[] };
    expect(jwks.keys).toHaveLength(1);
    expect(jwks.keys[0]).not.toHaveProperty("d");
  });

  it("/authorize は GitHub へ仲立ちの callback と包んだ state で送る", async () => {
    const res = await authorize();
    expect(res.status).toBe(302);
    const loc = new URL(res.headers.get("Location")!);
    expect(loc.origin + loc.pathname).toBe("https://github.com/login/oauth/authorize");
    expect(loc.searchParams.get("redirect_uri")).toBe(`${ISSUER}/callback`);
    expect(loc.searchParams.get("client_id")).toBe(GH_CLIENT);
    expect(loc.searchParams.get("scope")).toBe("read:user user:email");
    expect(loc.searchParams.get("state")).not.toBe("cognito-state");
  });

  it.each([
    [{ redirect_uri: "https://evil.example.test/cb" }],
    [{ client_id: "other-client" }],
  ])("/authorize は設定と違う値なら GitHub へ送らず 400: %j", async (params) => {
    const res = await authorize(params);
    expect(res.status).toBe(400);
    expect(res.headers.get("Location")).toBeNull();
    expect(await res.json()).toEqual({ error: "invalid_request" });
  });

  it("nonce が /callback を経て ID token に同じ値で出る。sub は GitHub の数の ID、aud はクライアント", async () => {
    const back = await fullCode();
    expect(back.origin + back.pathname).toBe(COGNITO_REDIRECT);
    expect(back.searchParams.get("state")).toBe("cognito-state");
    const res = await token(back.searchParams.get("code")!);
    expect(res.status).toBe(200);
    const body = (await res.json()) as { id_token: string; access_token: string };
    expect(body.access_token).toBe("gho_token");
    const jwks = createLocalJWKSet((await (await app.request(path("/jwks.json"))).json()) as { keys: JWK[] });
    const { payload } = await jwtVerify(body.id_token, jwks, { issuer: ISSUER, audience: GH_CLIENT });
    expect(payload).toMatchObject({ sub: "4242", nonce: "cognito-nonce" });
    expect(payload).not.toHaveProperty("email");
    expect(gh.requests.find((r) => r.url === GITHUB_TOKEN_URL)?.body).toContain(`redirect_uri=${encodeURIComponent(`${ISSUER}/callback`)}`);
  });

  it("nonce を受けなければ ID token に載せない", async () => {
    const q = new URLSearchParams({ client_id: GH_CLIENT, redirect_uri: COGNITO_REDIRECT, state: "s" });
    const loc = new URL((await app.request(path(`/authorize?${q}`))).headers.get("Location")!);
    const back = new URL((await callback(loc.searchParams.get("state")!)).headers.get("Location")!);
    const body = (await (await token(back.searchParams.get("code")!)).json()) as { id_token: string };
    expect(decodeJwt(body.id_token)).not.toHaveProperty("nonce");
  });

  it("GitHub がエラーを返したら、error を元の state と一緒に Cognito へ返す", async () => {
    const loc = new URL((await authorize()).headers.get("Location")!);
    const res = await callback(loc.searchParams.get("state")!, { error: "access_denied" });
    const back = new URL(res.headers.get("Location")!);
    expect(back.origin + back.pathname).toBe(COGNITO_REDIRECT);
    expect(Object.fromEntries(back.searchParams)).toEqual({ state: "cognito-state", error: "access_denied" });
  });

  async function forged(typ: string, claims: Record<string, unknown>, opts: { key?: SigningKeys; exp?: number } = {}) {
    const k = opts.key ?? keys;
    return new SignJWT(claims)
      .setProtectedHeader({ alg: "RS256", kid: k.publicJwk.kid, typ })
      .setIssuer(ISSUER)
      .setAudience(ISSUER)
      .setIssuedAt()
      .setExpirationTime(opts.exp ?? "5m")
      .sign(k.privateKey);
  }

  const wrapped = { client_id: GH_CLIENT, redirect_uri: COGNITO_REDIRECT, state: "s", nonce: "n" };

  it.each([
    ["署名の壊れた", () => forged("ndf-github-state+jwt", wrapped, { key: otherKeys })],
    ["期限の切れた", () => forged("ndf-github-state+jwt", wrapped, { exp: Math.floor(Date.now() / 1000) - 10 })],
    ["client_id の違う", () => forged("ndf-github-state+jwt", { ...wrapped, client_id: "x" })],
    ["redirect_uri の違う", () => forged("ndf-github-state+jwt", { ...wrapped, redirect_uri: "https://evil.example.test/cb" })],
    ["code として包んだ", () => forged("ndf-github-code+jwt", wrapped)],
  ])("%s state は /callback が断る", async (_name, make) => {
    const res = await callback(await make());
    expect(res.status).toBe(400);
    expect(res.headers.get("Location")).toBeNull();
  });

  const code = { gh_code: "gh-code", client_id: GH_CLIENT, redirect_uri: COGNITO_REDIRECT, nonce: "n" };

  it.each([
    ["署名の壊れた", () => forged("ndf-github-code+jwt", code, { key: otherKeys }), {}],
    ["期限の切れた", () => forged("ndf-github-code+jwt", code, { exp: Math.floor(Date.now() / 1000) - 10 }), {}],
    ["state として包んだ", () => forged("ndf-github-state+jwt", code), {}],
    ["client_id の違う要求の", () => forged("ndf-github-code+jwt", code), { client_id: "other" }],
    ["redirect_uri の違う要求の", () => forged("ndf-github-code+jwt", code), { redirect_uri: "https://evil.example.test/cb" }],
  ])("%s code は /token が invalid_grant で断り、GitHub を呼ばない", async (_name, make, form) => {
    const res = await token(await make(), form);
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "invalid_grant" });
    expect(gh.requests).toHaveLength(0);
  });

  it("/userinfo は検証済みの主のメールだけを返す", async () => {
    const get = async () => (await app.request(path("/userinfo"), { headers: { Authorization: "Bearer gho_token" } })).json();
    expect(await get()).toEqual({ sub: "4242", name: "octo", preferred_username: "octo", email: "me@example.test", email_verified: true });
    gh.emails = [{ email: "me@example.test", primary: true, verified: false }];
    expect(await get()).toEqual({ sub: "4242", name: "octo", preferred_username: "octo" });
    expect((await app.request(path("/userinfo"))).status).toBe(401);
  });

  it("応答に client_secret と署名の鍵を出さない", async () => {
    const back = await fullCode();
    const text = await (await token(back.searchParams.get("code")!)).text();
    expect(text).not.toContain(GH_SECRET);
    expect(text).not.toContain('"d"');
  });
});
