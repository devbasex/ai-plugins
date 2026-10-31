import { beforeEach, describe, expect, it } from "vitest";
import { AccountService } from "../accounts/service.ts";
import { MemoryAccountStore } from "../accounts/memory-store.ts";
import { CredentialService, type SecretsPort } from "../credentials/service.ts";
import { createApiApp, type LogEntry } from "../handlers/api.ts";
import { RepositoryService } from "../repositories/service.ts";
import { CANDIDATE_FALLBACK_NOTE, type CandidateModel } from "../runs/candidates.ts";
import { createRunsHarness, type RunsHarness } from "../testing/runs.ts";
import { createTokenKit, type TokenKit } from "../testing/tokens.ts";
import type { TestResponse } from "../testing/app.ts";

const API_KEY = "sk-ant-test-SECRET-VALUE-0001";

class FakeSecrets implements SecretsPort {
  readonly values = new Map<string, string>();
  fail = false;
  private n = 0;
  async create(name: string, value: string) {
    if (this.fail) throw new Error("AccessDenied");
    const arn = `arn:aws:secretsmanager:us-east-1:111111111111:secret:${name}-${++this.n}`;
    this.values.set(arn, value);
    return arn;
  }
  async put(arn: string, value: string) {
    if (this.fail) throw new Error("AccessDenied");
    this.values.set(arn, value);
  }
  async delete(arn: string) {
    if (this.fail) throw new Error("AccessDenied");
    this.values.delete(arn);
  }
}

let h: RunsHarness;
let tokens: TokenKit;
let secrets: FakeSecrets;
let model: { fn: CandidateModel };
let logs: LogEntry[];
let accounts: AccountService;
let app: ReturnType<typeof createApiApp>;
const bodies: string[] = [];

async function call(method: string, path: string, user: string, body?: unknown): Promise<TestResponse> {
  const res = (await app.request(path, {
    method,
    headers: { Authorization: await tokens.bearer(user), ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })) as TestResponse;
  bodies.push(await res.clone().text());
  return res;
}

async function personal(user: string): Promise<string> {
  return ((await (await call("GET", "/me", user)).json()) as { personal_account_id: string }).personal_account_id;
}

beforeEach(async () => {
  h = await createRunsHarness();
  tokens = await createTokenKit();
  secrets = new FakeSecrets();
  logs = [];
  bodies.length = 0;
  model = {
    fn: async (_source, issues) => issues.map((i, n) => ({ number: i.number, selected: n === 0, reason: `理由 ${i.number}` })),
  };
  const store = new MemoryAccountStore();
  let n = 0;
  accounts = new AccountService({ store, newId: () => `00000000-0000-4000-8000-${String(++n).padStart(12, "0")}` });
  const key = new TextEncoder().encode("proof-key-for-tests-0123456789ab");
  const factory = {
    repositories: new RepositoryService({ table: h.table, github: h.github, proofKey: async () => key }),
    credentials: new CredentialService(h.table, secrets),
    control: h.control,
    table: h.table,
    github: h.github,
    candidateModel: (s: "ranking" | "recent", i: { number: number; title: string }[]) => model.fn(s, i),
  };
  app = createApiApp(() => ({ verifier: tokens.verifier, accounts }), (e) => logs.push(e), () => null, () => factory);
  h.github.installations.set(9, new Map([[101, "org-a/repo-a"], [102, "org-a/repo-b"]]));
  h.github.installations.set(10, new Map([[201, "org-x/other"]]));
  h.github.userInstallations.set("user-token-1", [9]);
  h.github.codes.set("code-1", "user-token-1");
});

async function candidates(accountId: string, user = "u-1") {
  const url = (await (await call("GET", `/accounts/${accountId}/github/authorize-url`, user)).json()).url as string;
  const state = new URL(url).searchParams.get("state");
  return call("POST", `/accounts/${accountId}/github/candidates`, user, { code: "code-1", state });
}

async function register(accountId: string, repositoryId = 101) {
  const { proof } = await (await candidates(accountId)).json();
  return call("POST", `/accounts/${accountId}/repositories`, "u-1", { proof, repository_id: repositoryId });
}

describe("取り込み（受け入れ条件 1〜5、I32・I33・I36）", () => {
  it("導入の案内と、利用者の届くリポジトリだけの候補が出る（AC1）", async () => {
    const acc = await personal("u-1");
    expect((await (await call("GET", `/accounts/${acc}/github/install-url`, "u-1")).json()).url).toContain("/installations/new");
    const body = await (await candidates(acc)).json();
    expect(body.candidates.map((c: { repository_id: number }) => c.repository_id).sort()).toEqual([101, 102]);
  });

  it("state の利用者が違えば 400（決定 12）", async () => {
    const acc = await personal("u-1");
    const res = await call("POST", `/accounts/${acc}/github/candidates`, "u-1", { code: "code-1", state: "x" });
    expect(res.status).toBe(400);
  });

  it("証明に無い ID・導入が届かない登録は 403 で行が増えない（AC2・I33）", async () => {
    const acc = await personal("u-1");
    const { proof } = await (await candidates(acc)).json();
    expect((await call("POST", `/accounts/${acc}/repositories`, "u-1", { proof, repository_id: 201 })).status).toBe(403);
    h.github.installations.get(9)!.delete(102);
    expect((await call("POST", `/accounts/${acc}/repositories`, "u-1", { proof, repository_id: 102 })).status).toBe(403);
    expect(h.table.registrations.size).toBe(0);
  });

  it("同じリポジトリの 2 回目と同時の 2 本は 409 で 1 行（AC3・I32）", async () => {
    const acc = await personal("u-1");
    expect((await register(acc)).status).toBe(201);
    expect((await register(acc)).status).toBe(409);
    const { proof } = await (await candidates(acc)).json();
    const both = await Promise.all([102, 102].map((id) => call("POST", `/accounts/${acc}/repositories`, "u-1", { proof, repository_id: id })));
    expect(both.map((r) => r.status).sort()).toEqual([201, 409]);
    expect(h.table.registrations.size).toBe(2);
  });

  it("終わりの状態でない実行があると外せず 409、終われば 204（AC4・I36）", async () => {
    const acc = await personal("u-1");
    await register(acc);
    h.github.openIssues.set("org-a/repo-a", [{ number: 1, title: "a" }]);
    const started = await call("POST", `/accounts/${acc}/sprint-runs`, "u-1", { repository_id: 101, issues: [1] });
    expect(started.status).toBe(201);
    const { sprint_run_id } = await started.json();
    const list = await (await call("GET", `/accounts/${acc}/repositories`, "u-1")).json();
    expect(list.repositories[0].active_sprint_runs).toBe(1);
    expect((await call("DELETE", `/accounts/${acc}/repositories/101`, "u-1")).status).toBe(409);
    expect((await call("POST", `/accounts/${acc}/sprint-runs/${sprint_run_id}/cancel`, "u-1")).status).toBe(200);
    expect((await call("DELETE", `/accounts/${acc}/repositories/101`, "u-1")).status).toBe(204);
    expect((await call("DELETE", `/accounts/${acc}/repositories/101`, "u-1")).status).toBe(404);
  });

  it("他人のアカウントの一覧・登録・外しは #2170 と同じ 403 の本文（AC5）", async () => {
    const acc = await personal("u-1");
    await personal("u-2");
    const forbidden = { error: { code: "forbidden", message: "このアカウントを見る権限がありません" } };
    for (const [method, path, body] of [
      ["GET", `/accounts/${acc}/repositories`, undefined],
      ["POST", `/accounts/${acc}/repositories`, { proof: "x", repository_id: 101 }],
      ["DELETE", `/accounts/${acc}/repositories/101`, undefined],
    ] as const) {
      const res = await call(method, path, "u-2", body);
      expect(res.status).toBe(403);
      expect(await res.json()).toEqual(forbidden);
    }
  });
});

describe("キーの持ち込み（受け入れ条件 6〜8、I38）", () => {
  it("2 種類の保存・差し替え・消去ができ、応答は 4 項目だけで値と ARN を返さない（AC6・AC7）", async () => {
    const acc = await personal("u-1");
    const saved = await (await call("PUT", `/accounts/${acc}/model-credential`, "u-1", { kind: "anthropic_api_key", api_key: API_KEY })).json();
    expect(Object.keys(saved).sort()).toEqual(["external_id", "kind", "registered", "updated_at"]);
    expect(saved).toMatchObject({ registered: true, kind: "anthropic_api_key" });
    const role = await call("PUT", `/accounts/${acc}/model-credential`, "u-1", {
      kind: "aws_role",
      role_arn: "arn:aws:iam::123456789012:role/ndf",
      region: "us-east-1",
    });
    expect((await role.json()).kind).toBe("aws_role");
    expect(secrets.values.size).toBe(1);
    expect(JSON.parse([...secrets.values.values()][0]!)).toMatchObject({ external_id: `ndf-factory:${acc}` });
    expect((await call("DELETE", `/accounts/${acc}/model-credential`, "u-1")).status).toBe(204);
    expect(secrets.values.size).toBe(0);
    expect(await (await call("GET", `/accounts/${acc}/model-credential`, "u-1")).json()).toEqual({
      registered: false,
      kind: null,
      updated_at: null,
      external_id: `ndf-factory:${acc}`,
    });
    const all = bodies.join("\n") + JSON.stringify(logs) + JSON.stringify(h.logs);
    expect(all).not.toContain(API_KEY);
    expect(all).not.toContain("secretsmanager");
  });

  it("外部 ID はアカウントごとに違う（AC8）", async () => {
    const a = await (await call("GET", `/accounts/${await personal("u-1")}/model-credential`, "u-1")).json();
    const b = await (await call("GET", `/accounts/${await personal("u-2")}/model-credential`, "u-2")).json();
    expect(a.external_id).not.toBe(b.external_id);
  });

  it("形の違う入力は 400、置き場に書けなければ 503", async () => {
    const acc = await personal("u-1");
    expect((await call("PUT", `/accounts/${acc}/model-credential`, "u-1", { kind: "aws_role", role_arn: "x", region: "us-east-1" })).status).toBe(400);
    secrets.fail = true;
    const res = await call("PUT", `/accounts/${acc}/model-credential`, "u-1", { kind: "anthropic_api_key", api_key: API_KEY });
    expect(res.status).toBe(503);
    expect((await res.json()).error.code).toBe("credential_unavailable");
  });
});

describe("課題の候補（受け入れ条件 9・10）", () => {
  it("順位の表があれば理由つきの候補、選び直しで外した番号は出ない（AC9・AC10）", async () => {
    const acc = await personal("u-1");
    await register(acc);
    h.github.rankings.set("org-a/repo-a", { milestone: "03 Factory", rows: [{ number: 5, title: "e" }, { number: 6, title: "f" }] });
    const res = await (await call("POST", `/accounts/${acc}/repositories/101/issue-candidates`, "u-1", {})).json();
    expect(res).toMatchObject({ source: "ranking", milestone: "03 Factory", note: null });
    expect(res.candidates.every((c: { reason: string }) => c.reason !== "")).toBe(true);
    const again = await (await call("POST", `/accounts/${acc}/repositories/101/issue-candidates`, "u-1", { exclude: [5] })).json();
    expect(again.candidates.map((c: { number: number }) => c.number)).toEqual([6]);
  });

  it("表が無ければ新しい順、Bedrock が失敗すると note を出し順のまま理由を付ける（AC9）", async () => {
    const acc = await personal("u-1");
    await register(acc);
    h.github.openIssues.set("org-a/repo-a", [{ number: 9, title: "i" }, { number: 8, title: "h" }]);
    model.fn = () => Promise.reject(new Error("ThrottlingException"));
    const res = await (await call("POST", `/accounts/${acc}/repositories/101/issue-candidates`, "u-1", {})).json();
    expect(res.source).toBe("recent");
    expect(res.note).toBe(CANDIDATE_FALLBACK_NOTE);
    expect(res.candidates.map((c: { number: number }) => c.number)).toEqual([9, 8]);
  });
});
