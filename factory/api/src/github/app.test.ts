import { generateKeyPairSync } from "node:crypto";
import { describe, expect, it } from "vitest";
import { GitHubApp } from "./app.ts";

const { privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const PEM = privateKey.export({ type: "pkcs1", format: "pem" }).toString();
const TOKEN = "ghs_filing_token_value_0123456789";

type Call = { url: string; method: string; body: unknown; authorization: string | null };

function app(responses: (call: Call) => Response) {
  const calls: Call[] = [];
  const fetchImpl = (async (input: string | URL | Request, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    const call = {
      url: String(input),
      method: init?.method ?? "GET",
      body: init?.body ? JSON.parse(String(init.body)) : null,
      authorization: headers.get("Authorization"),
    };
    calls.push(call);
    return responses(call);
  }) as typeof fetch;
  const github = new GitHubApp({
    appId: "1",
    slug: "ndf-factory-test",
    clientId: "c",
    clientSecret: async () => "s",
    privateKeyPem: async () => PEM,
    fetch: fetchImpl,
  });
  return { github, calls };
}

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("起票の代行の GitHub の口（#2216 の受け入れ条件 14、I47）", () => {
  it("起票先の 1 つと issues: write だけに絞った token で Issue を作り、token を返さない", async () => {
    const { github, calls } = app((call) =>
      call.url.endsWith("/access_tokens")
        ? json(201, { token: TOKEN, expires_at: "2026-10-31T02:00:00Z" })
        : json(201, { number: 5, html_url: "https://github.com/org-b/repo-b/issues/5", repository_url: "https://api.github.com/repos/org-b/repo-b" }),
    );
    const created = await github.createIssue(10, 2, { title: "題", body: "本文" });
    expect(created).toEqual({ repo: "org-b/repo-b", number: 5, url: "https://github.com/org-b/repo-b/issues/5" });
    expect(JSON.stringify(created)).not.toContain(TOKEN);
    expect(calls[0]).toMatchObject({
      url: "https://api.github.com/app/installations/10/access_tokens",
      method: "POST",
      body: { repository_ids: [2], permissions: { issues: "write" } },
    });
    expect(calls[1]).toMatchObject({
      url: "https://api.github.com/repositories/2/issues",
      method: "POST",
      body: { title: "題", body: "本文" },
      authorization: `Bearer ${TOKEN}`,
    });
  });

  it("token を作れなければ null で、Issue の作成を呼ばない", async () => {
    const { github, calls } = app(() => json(422, {}));
    expect(await github.createIssue(10, 2, { title: "題", body: "本文" })).toBeNull();
    expect(calls).toHaveLength(1);
  });

  it("作成の要求の失敗は投げ、例外の文に token を含まない", async () => {
    const { github } = app((call) => (call.url.endsWith("/access_tokens") ? json(201, { token: TOKEN, expires_at: "x" }) : json(502, {})));
    const err = await github.createIssue(10, 2, { title: "題", body: "本文" }).catch((e: unknown) => e as Error);
    expect(err).toBeInstanceOf(Error);
    expect((err as Error).message).not.toContain(TOKEN);
  });

  it("公開範囲は metadata: read だけの token で読む", async () => {
    const { github, calls } = app((call) =>
      call.url.endsWith("/access_tokens") ? json(201, { token: TOKEN, expires_at: "x" }) : json(200, { visibility: "public", private: false }),
    );
    expect(await github.repositoryVisibility(10, 2)).toBe("public");
    expect(calls[0]!.body).toEqual({ repository_ids: [2], permissions: { metadata: "read" } });
  });
});
