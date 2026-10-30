import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createAuth } from "./auth.ts";

const config = {
  cognitoDomain: "auth.example.test",
  cognitoClientId: "web-client",
  webUrl: "https://factory.example.test",
  apiUrl: "https://api.example.test",
};

function unsignedJwt(claims: Record<string, unknown>) {
  const enc = (o: object) => btoa(JSON.stringify(o)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
  return `${enc({ alg: "none", typ: "JWT" })}.${enc(claims)}.`;
}

let visited: string[];
beforeEach(() => {
  sessionStorage.clear();
  visited = [];
});
afterEach(() => vi.unstubAllGlobals());

const make = () => createAuth(config, sessionStorage, { assign: (url) => visited.push(url) });

describe("ログイン（受け入れ条件 1〜3）", () => {
  it.each(["Google", "GitHub"] as const)("%s のボタンは identity_provider と PKCE を付けて Cognito へ送る", async (idp) => {
    await make().login(idp);
    const url = new URL(visited[0]!);
    expect(url.origin + url.pathname).toBe("https://auth.example.test/oauth2/authorize");
    expect(url.searchParams.get("identity_provider")).toBe(idp);
    expect(url.searchParams.get("client_id")).toBe("web-client");
    expect(url.searchParams.get("redirect_uri")).toBe("https://factory.example.test/callback");
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
  });

  it("戻りの code を token に換えて保存する", async () => {
    const auth = make();
    await auth.login("Google");
    const authorize = new URL(visited[0]!);
    const tokenRequests: string[] = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      tokenRequests.push(String(input));
      expect(String(init?.body)).toContain("code_verifier=");
      return Response.json({
        access_token: "access-1",
        token_type: "Bearer",
        expires_in: 3600,
        id_token: unsignedJwt({ sub: "u-1", nonce: authorize.searchParams.get("nonce") ?? undefined }),
      });
    });
    const result = await auth.completeLogin(`https://factory.example.test/callback?code=c&state=${authorize.searchParams.get("state")}`);
    expect(result).toEqual({ ok: true });
    expect(tokenRequests).toEqual(["https://auth.example.test/oauth2/token"]);
    expect(auth.getToken()).toBe("access-1");
  });

  it("error つきの戻りは token を保存しない", async () => {
    const auth = make();
    await auth.login("GitHub");
    const state = new URL(visited[0]!).searchParams.get("state");
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    expect(await auth.completeLogin(`https://factory.example.test/callback?error=access_denied&state=${state}`)).toEqual({
      ok: false,
      error: "access_denied",
    });
    expect(auth.getToken()).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("ログアウトは token を消してから Cognito のログアウトへ送る", () => {
    const auth = make();
    sessionStorage.setItem("ndf-factory.access_token", "access-1");
    auth.logout();
    expect(auth.getToken()).toBeNull();
    const url = new URL(visited[0]!);
    expect(url.origin + url.pathname).toBe("https://auth.example.test/logout");
    expect(url.searchParams.get("logout_uri")).toBe("https://factory.example.test/login");
  });
});
