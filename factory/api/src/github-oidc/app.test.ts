import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import { createLocalJWKSet, decodeJwt, decodeProtectedHeader, generateKeyPair, jwtVerify, SignJWT, type JWK } from "jose";
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

// 現状固定テスト（createGithubOidcApp の分割の前に、経路ごとの振る舞いを固定する）
describe("GitHub の仲立ちの現状固定", () => {
  interface Fake {
    requests: { url: string; method?: string; headers: Record<string, string>; body?: string }[];
    tokenStatus: number;
    tokenBody: Record<string, unknown>;
    userStatus: number;
    user: Record<string, unknown>;
    emailsStatus: number;
    emails: unknown;
  }

  let fake: Fake;
  const fakeFetch: typeof fetch = async (input, init) => {
    const url = String(input);
    fake.requests.push({ url, method: init?.method, headers: Object.fromEntries(new Headers(init?.headers)), body: init?.body?.toString() });
    if (url === GITHUB_TOKEN_URL) return Response.json(fake.tokenBody, { status: fake.tokenStatus });
    if (url === `${GITHUB_API_URL}/user`) return Response.json(fake.user, { status: fake.userStatus });
    if (url === `${GITHUB_API_URL}/user/emails`) return Response.json(fake.emails, { status: fake.emailsStatus });
    return new Response("", { status: 404 });
  };
  beforeEach(() => {
    fake = {
      requests: [],
      tokenStatus: 200,
      tokenBody: { access_token: "gho_token", scope: "read:user" },
      userStatus: 200,
      user: { id: 7, login: "octo", name: "Octo Cat" },
      emailsStatus: 200,
      emails: [{ email: "me@example.test", primary: true, verified: true }],
    };
  });

  const make = (issuer = ISSUER) =>
    createGithubOidcApp({ issuer, cognitoDomain: COGNITO_DOMAIN, githubClientId: GH_CLIENT }, { keys: async () => keys, fetch: fakeFetch });

  async function codeFor(a: ReturnType<typeof make>, base: string, q: Record<string, string> = { state: "s", nonce: "n" }) {
    const params = new URLSearchParams({ client_id: GH_CLIENT, redirect_uri: COGNITO_REDIRECT, ...q });
    const loc = new URL((await a.request(`${base}/authorize?${params}`)).headers.get("Location")!);
    const res = await a.request(`${base}/callback?${new URLSearchParams({ state: loc.searchParams.get("state")!, code: "gh-code" })}`);
    return new URL(res.headers.get("Location")!).searchParams.get("code")!;
  }

  function postToken(a: ReturnType<typeof make>, form: Record<string, string>, headers: Record<string, string> = {}) {
    return a.request("/oidc/github/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", ...headers },
      body: new URLSearchParams(form),
    });
  }

  it("発見の文書の全体", async () => {
    const conf = await (await make().request("/oidc/github/.well-known/openid-configuration")).json();
    expect(conf).toEqual({
      issuer: ISSUER,
      authorization_endpoint: `${ISSUER}/authorize`,
      token_endpoint: `${ISSUER}/token`,
      userinfo_endpoint: `${ISSUER}/userinfo`,
      jwks_uri: `${ISSUER}/jwks.json`,
      response_types_supported: ["code"],
      subject_types_supported: ["public"],
      id_token_signing_alg_values_supported: ["RS256"],
      scopes_supported: ["openid", "profile", "email"],
      token_endpoint_auth_methods_supported: ["client_secret_post", "client_secret_basic"],
    });
  });

  it("発行元の末尾の / は落とす", async () => {
    const conf = (await (await make(`${ISSUER}/`).request("/oidc/github/.well-known/openid-configuration")).json()) as { issuer: string };
    expect(conf.issuer).toBe(ISSUER);
    const loc = new URL((await make(`${ISSUER}/`).request(`/oidc/github/authorize?${new URLSearchParams({ client_id: GH_CLIENT, redirect_uri: COGNITO_REDIRECT })}`)).headers.get("Location")!);
    expect(loc.searchParams.get("redirect_uri")).toBe(`${ISSUER}/callback`);
  });

  it("発行元の path が無ければ根に置く", async () => {
    const a = make("https://idp.example.test");
    const conf = (await (await a.request("/.well-known/openid-configuration")).json()) as Record<string, string>;
    expect(conf.issuer).toBe("https://idp.example.test");
    expect(conf.jwks_uri).toBe("https://idp.example.test/jwks.json");
    expect((await a.request("/oidc/github/jwks.json")).status).toBe(404);
  });

  it("jwks.json は公開鍵の JWK をそのまま 1 つ返す", async () => {
    expect(await (await make().request("/oidc/github/jwks.json")).json()).toEqual({ keys: [keys.publicJwk] });
  });

  it("/authorize の包んだ state の中身と見出し", async () => {
    const q = new URLSearchParams({ client_id: GH_CLIENT, redirect_uri: COGNITO_REDIRECT, state: "s", nonce: "n" });
    const loc = new URL((await make().request(`/oidc/github/authorize?${q}`)).headers.get("Location")!);
    expect([...loc.searchParams.keys()]).toEqual(["client_id", "redirect_uri", "scope", "state"]);
    const st = loc.searchParams.get("state")!;
    expect(decodeProtectedHeader(st)).toEqual({ alg: "RS256", kid: keys.publicJwk.kid, typ: "ndf-github-state+jwt" });
    const p = decodeJwt(st);
    expect(p).toMatchObject({ client_id: GH_CLIENT, redirect_uri: COGNITO_REDIRECT, state: "s", nonce: "n", iss: ISSUER, aud: ISSUER });
    expect(p.exp! - p.iat!).toBe(600);
  });

  it("/authorize は client_id と redirect_uri の無い要求を 400 で断る", async () => {
    const res = await make().request("/oidc/github/authorize");
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "invalid_request" });
  });

  it("/callback の包んだ code の中身と見出し", async () => {
    const c = await codeFor(make(), "/oidc/github");
    expect(decodeProtectedHeader(c)).toEqual({ alg: "RS256", kid: keys.publicJwk.kid, typ: "ndf-github-code+jwt" });
    const p = decodeJwt(c);
    expect(p).toMatchObject({ gh_code: "gh-code", client_id: GH_CLIENT, redirect_uri: COGNITO_REDIRECT, nonce: "n", iss: ISSUER, aud: ISSUER });
    expect(p).not.toHaveProperty("state");
    expect(p.exp! - p.iat!).toBe(300);
  });

  it("/callback は state が無ければ 400、code も error も無ければ access_denied、元の state が無ければ state を付けない", async () => {
    const a = make();
    const none = await a.request("/oidc/github/callback?code=gh-code");
    expect(none.status).toBe(400);
    expect(await none.json()).toEqual({ error: "invalid_request" });

    const q = new URLSearchParams({ client_id: GH_CLIENT, redirect_uri: COGNITO_REDIRECT });
    const loc = new URL((await a.request(`/oidc/github/authorize?${q}`)).headers.get("Location")!);
    const res = await a.request(`/oidc/github/callback?${new URLSearchParams({ state: loc.searchParams.get("state")! })}`);
    expect(res.status).toBe(302);
    expect(res.headers.get("Location")).toBe(`${COGNITO_REDIRECT}?error=access_denied`);
    const ok = await a.request(`/oidc/github/callback?${new URLSearchParams({ state: loc.searchParams.get("state")!, code: "x" })}`);
    expect([...new URL(ok.headers.get("Location")!).searchParams.keys()]).toEqual(["code"]);
  });

  it("/token の応答・ID token・GitHub への要求の全体", async () => {
    const a = make();
    const c = await codeFor(a, "/oidc/github");
    const res = await postToken(a, { grant_type: "authorization_code", code: c, redirect_uri: COGNITO_REDIRECT, client_id: GH_CLIENT, client_secret: GH_SECRET });
    expect(res.status).toBe(200);
    const body = (await res.json()) as Record<string, string>;
    expect(Object.keys(body)).toEqual(["access_token", "token_type", "id_token", "scope"]);
    expect(body).toMatchObject({ access_token: "gho_token", token_type: "Bearer", scope: "read:user" });
    expect(decodeProtectedHeader(body.id_token!)).toEqual({ alg: "RS256", kid: keys.publicJwk.kid, typ: "JWT" });
    const p = decodeJwt(body.id_token!);
    expect(Object.keys(p).sort()).toEqual(["aud", "exp", "iat", "iss", "nonce", "sub"]);
    expect(p).toMatchObject({ iss: ISSUER, sub: "7", aud: GH_CLIENT, nonce: "n" });
    expect(p.exp! - p.iat!).toBe(300);

    expect(fake.requests.map((r) => r.url)).toEqual([GITHUB_TOKEN_URL, `${GITHUB_API_URL}/user`]);
    const [tok, user] = fake.requests;
    expect(tok!.method).toBe("POST");
    expect(tok!.headers).toEqual({ accept: "application/json", "content-type": "application/x-www-form-urlencoded" });
    expect(Object.fromEntries(new URLSearchParams(tok!.body))).toEqual({ client_id: GH_CLIENT, client_secret: GH_SECRET, code: "gh-code", redirect_uri: `${ISSUER}/callback` });
    expect(user!.method).toBeUndefined();
    expect(user!.headers).toEqual({ authorization: "Bearer gho_token", accept: "application/vnd.github+json", "user-agent": "ndf-factory" });
  });

  it("/token は scope が無ければ空文字を返す", async () => {
    fake.tokenBody = { access_token: "gho_token" };
    const a = make();
    const c = await codeFor(a, "/oidc/github");
    const body = (await (await postToken(a, { grant_type: "authorization_code", code: c, redirect_uri: COGNITO_REDIRECT, client_id: GH_CLIENT, client_secret: GH_SECRET })).json()) as Record<string, string>;
    expect(body.scope).toBe("");
  });

  it("/token は Basic 認証のクライアントを受け、本文の値より優先する", async () => {
    const a = make();
    const c = await codeFor(a, "/oidc/github");
    const basic = Buffer.from(`${encodeURIComponent(GH_CLIENT)}:${encodeURIComponent("s:e c")}`).toString("base64");
    const res = await postToken(a, { grant_type: "authorization_code", code: c, redirect_uri: COGNITO_REDIRECT, client_id: "ignored", client_secret: "ignored" }, { Authorization: `Basic ${basic}` });
    expect(res.status).toBe(200);
    expect(new URLSearchParams(fake.requests[0]!.body).get("client_secret")).toBe("s:e c");
  });

  it.each([
    ["grant_type の違う", { grant_type: "client_credentials" }],
    ["client_id の無い", { client_id: "" }],
    ["client_secret の無い", { client_secret: "" }],
  ])("/token は %s 要求を invalid_request で断る", async (_name, over) => {
    const a = make();
    const c = await codeFor(a, "/oidc/github");
    const res = await postToken(a, { grant_type: "authorization_code", code: c, redirect_uri: COGNITO_REDIRECT, client_id: GH_CLIENT, client_secret: GH_SECRET, ...over });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "invalid_request" });
    expect(fake.requests).toHaveLength(0);
  });

  it("/token は code の無い要求を invalid_grant で断る", async () => {
    const res = await postToken(make(), { grant_type: "authorization_code", redirect_uri: COGNITO_REDIRECT, client_id: GH_CLIENT, client_secret: GH_SECRET });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "invalid_grant" });
  });

  it.each([
    ["token の応答が失敗", (f: Fake) => { f.tokenStatus = 500; }, 1],
    ["token の応答に access_token が無い", (f: Fake) => { f.tokenBody = { error: "bad_verification_code" }; }, 1],
    ["利用者の応答が失敗", (f: Fake) => { f.userStatus = 401; }, 2],
    ["利用者の id が数でない", (f: Fake) => { f.user = { id: "7", login: "octo" }; }, 2],
  ])("/token は GitHub の%sなら invalid_grant", async (_name, set, calls) => {
    set(fake);
    const a = make();
    const c = await codeFor(a, "/oidc/github");
    const res = await postToken(a, { grant_type: "authorization_code", code: c, redirect_uri: COGNITO_REDIRECT, client_id: GH_CLIENT, client_secret: GH_SECRET });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "invalid_grant" });
    expect(fake.requests).toHaveLength(calls);
  });

  it("/userinfo は POST も受け、name を返し、メールの読み出しの失敗ではメールを出さない", async () => {
    const a = make();
    const post = await a.request("/oidc/github/userinfo", { method: "POST", headers: { Authorization: "Bearer t" } });
    expect(await post.json()).toEqual({ sub: "7", name: "Octo Cat", preferred_username: "octo", email: "me@example.test", email_verified: true });
    expect(fake.requests.map((r) => r.url)).toEqual([`${GITHUB_API_URL}/user`, `${GITHUB_API_URL}/user/emails`]);
    expect(fake.requests[1]!.headers).toEqual({ authorization: "Bearer t", accept: "application/vnd.github+json", "user-agent": "ndf-factory" });
    fake.emailsStatus = 500;
    expect(await (await a.request("/oidc/github/userinfo", { headers: { Authorization: "Bearer t" } })).json()).toEqual({ sub: "7", name: "Octo Cat", preferred_username: "octo" });
    fake.emailsStatus = 200;
    fake.emails = [{ email: "a@example.test", primary: false, verified: true }, { email: "b@example.test", primary: true, verified: true }];
    expect(await (await a.request("/oidc/github/userinfo", { headers: { Authorization: "Bearer t" } })).json()).toMatchObject({ email: "b@example.test" });
  });

  it.each([
    ["Bearer でない見出し", { Authorization: "Basic abc" }, (_f: Fake) => {}, 0],
    ["利用者の応答の失敗", { Authorization: "Bearer t" }, (f: Fake) => { f.userStatus = 401; }, 1],
    ["数でない id", { Authorization: "Bearer t" }, (f: Fake) => { f.user = { login: "octo" }; }, 1],
  ])("/userinfo は %s で 401 invalid_token", async (_name, headers, set, calls) => {
    set(fake);
    const res = await make().request("/oidc/github/userinfo", { headers });
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ error: "invalid_token" });
    expect(fake.requests).toHaveLength(calls);
  });

  it("/userinfo は PUT を受けない", async () => {
    expect((await make().request("/oidc/github/userinfo", { method: "PUT", headers: { Authorization: "Bearer t" } })).status).toBe(404);
  });
});
