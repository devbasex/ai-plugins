import { beforeAll, describe, expect, it } from "vitest";
import { createTestApi, type TestApi } from "../testing/app.ts";
import { CLIENT_ID, OTHER_POOL_ID } from "../testing/tokens.ts";

describe("token の検証（受け入れ条件 4）", () => {
  let api: TestApi;
  beforeAll(async () => {
    api = await createTestApi();
    // 先に正しい利用者を作り、401 の本文にアカウントの中身が出ないことを確かめられるようにする
    expect((await api.call("GET", "/me", { user: "u-1" })).status).toBe(200);
  });

  const cases: [string, (a: TestApi) => Promise<string | undefined>][] = [
    ["token が無い", async () => undefined],
    ["Bearer の形でない", async (a) => a.tokens.sign({ sub: "u-1", username: "Google_u-1" })],
    ["署名が違う", async (a) => `Bearer ${await a.tokens.sign({ sub: "u-1", username: "Google_u-1" }, { key: "other" })}`],
    ["期限切れ", async (a) => `Bearer ${await a.tokens.sign({ sub: "u-1", username: "Google_u-1" }, { expiresIn: Math.floor(Date.now() / 1000) - 60 })}`],
    [
      "別の利用者プール",
      async (a) =>
        `Bearer ${await a.tokens.sign({ sub: "u-1", username: "Google_u-1", iss: `https://cognito-idp.us-east-1.amazonaws.com/${OTHER_POOL_ID}` })}`,
    ],
    ["別のクライアント", async (a) => `Bearer ${await a.tokens.sign({ sub: "u-1", username: "Google_u-1", client_id: "other-client" })}`],
    ["ID token", async (a) => `Bearer ${await a.tokens.sign({ sub: "u-1", username: "Google_u-1", token_use: "id", aud: CLIENT_ID })}`],
    ["知らない IdP", async (a) => `Bearer ${await a.tokens.sign({ sub: "u-1", username: "Facebook_u-1" })}`],
  ];

  it.each(cases)("%s なら 401 で、アカウントの中身を返さない", async (_name, make) => {
    const authorization = await make(api);
    const res = await api.call("GET", "/me", authorization === undefined ? {} : { authorization });
    expect(res.status).toBe(401);
    const body = await res.json();
    expect(body).toEqual({ error: { code: "unauthorized", message: expect.any(String) } });
    expect(JSON.stringify(body)).not.toContain("00000000-");
  });

  it("正しい token は通り、IdP を username の頭から決める", async () => {
    const res = await api.call("GET", "/me", { user: "u-2", idp: "GitHub" });
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ user_id: "u-2", idp: "github" });
  });

  it("ログには利用者の ID・経路・状態コードだけを出し、token を出さない", async () => {
    const auth = await api.tokens.bearer("u-3");
    await api.call("GET", "/me", { authorization: auth });
    const text = JSON.stringify(api.logs);
    expect(text).not.toContain(auth.slice("Bearer ".length));
    expect(api.logs.at(-1)).toEqual({ user_id: "u-3", method: "GET", path: "/me", status: 200 });
  });
});
