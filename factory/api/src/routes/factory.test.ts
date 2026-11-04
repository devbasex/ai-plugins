import { beforeEach, describe, expect, it } from "vitest";
import { AccountService } from "../accounts/service.ts";
import { MemoryAccountStore } from "../accounts/memory-store.ts";
import { CredentialService, type SecretsPort } from "../credentials/service.ts";
import { createApiApp, type LogEntry } from "../handlers/api.ts";
import { RepositoryService } from "../repositories/service.ts";
import { memoryProgressReader } from "../progress/read.ts";
import { CANDIDATE_FALLBACK_NOTE, type CandidateModel } from "../runs/candidates.ts";
import { LoopControl } from "../runs/loops.ts";
import { createRunsHarness, type RunsHarness } from "../testing/runs.ts";
import { createTokenKit, type TokenKit } from "../testing/tokens.ts";
import type { OperationId } from "@ndf-factory/contracts/operations";
import { firstResult, opRequest, type OpOptions, type TestResponse } from "../testing/app.ts";

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

// 操作を呼び、要素 1 個の結果を状態と本文で読む（#2384）
async function op(operationId: OperationId, user: string, opts: Omit<OpOptions, "user"> = {}): Promise<{ status: number; body: any }> {
  const { method, path, body } = opRequest(operationId, opts);
  return firstResult(await call(method, path, user, body));
}

let keys = 0;
const idem = () => `k-${++keys}`;

async function personal(user: string): Promise<string> {
  return (await op("getMe", user)).body.personal_account_id;
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
    loops: new LoopControl({ table: h.table, control: h.control, reader: memoryProgressReader(h.progress), now: () => h.clock.now }),
    organizer: h.organizer,
    table: h.table,
    github: h.github,
    candidateModel: (s: "ranking" | "recent", i: { number: number; title: string }[]) => model.fn(s, i),
    reader: memoryProgressReader(h.progress),
  };
  app = createApiApp(() => ({ verifier: tokens.verifier, accounts }), (e) => logs.push(e), () => null, () => factory);
  h.github.installations.set(9, new Map([[101, "org-a/repo-a"], [102, "org-a/repo-b"]]));
  h.github.installations.set(10, new Map([[201, "org-x/other"]]));
  h.github.userInstallations.set("user-token-1", [9]);
  h.github.codes.set("code-1", "user-token-1");
});

async function candidates(accountId: string, user = "u-1") {
  const url = (await op("getAuthorizeUrl", user, { accountId })).body.url as string;
  const state = new URL(url).searchParams.get("state");
  return op("postRepositoryCandidates", user, { accountId, items: [{ code: "code-1", state }] });
}

async function register(accountId: string, repositoryId = 101) {
  const { proof } = (await candidates(accountId)).body;
  return op("registerRepository", "u-1", { accountId, items: [{ proof, repository_id: repositoryId }] });
}

describe("取り込み（受け入れ条件 1〜5、I32・I33・I36）", () => {
  it("導入の案内と、利用者の届くリポジトリだけの候補が出る（AC1）", async () => {
    const acc = await personal("u-1");
    expect((await op("getInstallUrl", "u-1", { accountId: acc })).body.url).toContain("/installations/new");
    const { body } = await candidates(acc);
    expect(body.candidates.map((c: { repository_id: number }) => c.repository_id).sort()).toEqual([101, 102]);
  });

  it("state の利用者が違えば 400（決定 12）", async () => {
    const acc = await personal("u-1");
    expect((await op("postRepositoryCandidates", "u-1", { accountId: acc, items: [{ code: "code-1", state: "x" }] })).status).toBe(400);
  });

  it("鍵を持たない操作に 2 個の要素を送ると、どれも処理せずに 400 duplicate_key", async () => {
    const acc = await personal("u-1");
    const res = await call("POST", `/accounts/${acc}/github/candidates`, "u-1", [{ code: "code-1", state: "x" }, { code: "code-1", state: "y" }]);
    expect(res.status).toBe(400);
    expect((await res.json()).error.code).toBe("duplicate_key");
  });

  it("証明に無い ID・導入が届かない登録は 403 で行が増えない（AC2・I33）", async () => {
    const acc = await personal("u-1");
    const { proof } = (await candidates(acc)).body;
    expect((await op("registerRepository", "u-1", { accountId: acc, items: [{ proof, repository_id: 201 }] })).status).toBe(403);
    h.github.installations.get(9)!.delete(102);
    expect((await op("registerRepository", "u-1", { accountId: acc, items: [{ proof, repository_id: 102 }] })).status).toBe(403);
    expect(h.table.registrations.size).toBe(0);
  });

  it("同じリポジトリの 2 回目と同時の 2 本は 409 で 1 行（AC3・I32）", async () => {
    const acc = await personal("u-1");
    expect((await register(acc)).status).toBe(200);
    expect((await register(acc)).status).toBe(409);
    const { proof } = (await candidates(acc)).body;
    const both = await Promise.all([102, 102].map((id) => op("registerRepository", "u-1", { accountId: acc, items: [{ proof, repository_id: id }] })));
    expect(both.map((r) => r.status).sort()).toEqual([200, 409]);
    expect(h.table.registrations.size).toBe(2);
  });

  it("終わりの状態でない実行があると外せず 409、終われば外せる（AC4・I36）", async () => {
    const acc = await personal("u-1");
    await register(acc);
    h.github.openIssues.set("org-a/repo-a", [{ number: 1, title: "a" }]);
    const started = await op("startSprintRun", "u-1", { accountId: acc, items: [{ repository_id: 101, issues: [1], idempotency_key: idem() }] });
    expect(started.status).toBe(200);
    const { sprint_run_id } = started.body;
    const list = await (await call("GET", `/accounts/${acc}/repositories`, "u-1")).json();
    expect(list[0].active_sprint_runs).toBe(1);
    const remove = () => op("removeRepository", "u-1", { accountId: acc, query: { repository_id: [101] } });
    expect((await remove()).status).toBe(409);
    expect((await op("cancelSprintRun", "u-1", { accountId: acc, items: [{ sprint_run_id }] })).status).toBe(200);
    expect(await remove()).toEqual({ status: 200, body: { removed: true } });
    expect((await remove()).status).toBe(404);
  });

  it("他人のアカウントの一覧・登録・外しは #2170 と同じ 403 の本文（AC5）", async () => {
    const acc = await personal("u-1");
    await personal("u-2");
    const forbidden = { error: { code: "forbidden", message: "このアカウントを見る権限がありません" } };
    for (const result of [
      await op("listRepositories", "u-2", { accountId: acc }),
      await op("registerRepository", "u-2", { accountId: acc, items: [{ proof: "x", repository_id: 101 }] }),
      await op("removeRepository", "u-2", { accountId: acc, query: { repository_id: [101] } }),
    ]) {
      expect(result).toEqual({ status: 403, body: forbidden });
    }
  });
});

describe("作成の冪等の鍵（#2384 の受け入れ条件 9・I14）", () => {
  async function ready() {
    const acc = await personal("u-1");
    await register(acc);
    h.github.openIssues.set("org-a/repo-a", [{ number: 1, title: "a" }, { number: 2, title: "b" }]);
    return acc;
  }

  it("同じ鍵の 2 回目は 1 件だけ作り、同じ識別子を返す", async () => {
    const acc = await ready();
    const item = { repository_id: 101, issues: [1], idempotency_key: "same-key" };
    const first = await op("startSprintRun", "u-1", { accountId: acc, items: [item] });
    const second = await op("startSprintRun", "u-1", { accountId: acc, items: [item] });
    expect(second.body.sprint_run_id).toBe(first.body.sprint_run_id);
    expect(h.table.runs.size).toBe(1);
  });

  it("違う作成の操作へ同じ鍵を送ると、それぞれ作る", async () => {
    const acc = await ready();
    await op("startSprintRun", "u-1", { accountId: acc, items: [{ repository_id: 101, issues: [1], idempotency_key: "shared" }] });
    const loop = await op("startLoop", "u-1", { accountId: acc, items: [{ repository_id: 101, name: "l1", milestones: ["m"], idempotency_key: "shared" }] });
    expect(loop.status).toBe(200);
    expect(h.table.loops.size).toBe(1);
    expect(h.table.runs.size).toBeGreaterThanOrEqual(1);
  });

  it("鍵の行の期限（24 時間）を過ぎた同じ鍵は、行が残っていても新しく作る", async () => {
    const acc = await ready();
    const first = await op("startSprintRun", "u-1", { accountId: acc, items: [{ repository_id: 101, issues: [1], idempotency_key: "old" }] });
    // 1 本目を終わらせ、同時の実行の数の上限に当たらないようにする
    await h.table.finishRun(first.body.sprint_run_id, "done", null, h.clock.now.toISOString());
    h.clock.now = new Date(h.clock.now.getTime() + 24 * 60 * 60 * 1000 + 1000);
    const second = await op("startSprintRun", "u-1", { accountId: acc, items: [{ repository_id: 101, issues: [2], idempotency_key: "old" }] });
    expect(second.status).toBe(200);
    expect(second.body.sprint_run_id).not.toBe(first.body.sprint_run_id);
    expect(h.table.idempotency.size).toBe(1);
  });

  it("鍵の形が違えば要素だけを 400 にする", async () => {
    const acc = await ready();
    expect((await op("startSprintRun", "u-1", { accountId: acc, items: [{ repository_id: 101, issues: [1], idempotency_key: "空白 あり" }] })).status).toBe(400);
    expect(h.table.runs.size).toBe(0);
  });
});

describe("キーの持ち込み（受け入れ条件 6〜8、I38）", () => {
  const put = (acc: string, item: unknown) => op("putModelCredential", "u-1", { accountId: acc, items: [item] });

  it("2 種類の保存・差し替え・消去ができ、応答は 4 項目だけで値と ARN を返さない（AC6・AC7）", async () => {
    const acc = await personal("u-1");
    const saved = (await put(acc, { kind: "anthropic_api_key", api_key: API_KEY })).body;
    expect(Object.keys(saved).sort()).toEqual(["external_id", "kind", "registered", "updated_at"]);
    expect(saved).toMatchObject({ registered: true, kind: "anthropic_api_key" });
    const role = await put(acc, { kind: "aws_role", role_arn: "arn:aws:iam::123456789012:role/ndf", region: "us-east-1" });
    expect(role.body.kind).toBe("aws_role");
    expect(secrets.values.size).toBe(1);
    expect(JSON.parse([...secrets.values.values()][0]!)).toMatchObject({ external_id: `ndf-factory:${acc}` });
    expect(await op("deleteModelCredential", "u-1", { accountId: acc })).toEqual({ status: 200, body: { removed: true } });
    expect(secrets.values.size).toBe(0);
    expect((await op("getModelCredential", "u-1", { accountId: acc })).body).toEqual({
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
    const a = (await op("getModelCredential", "u-1", { accountId: await personal("u-1") })).body;
    const b = (await op("getModelCredential", "u-2", { accountId: await personal("u-2") })).body;
    expect(a.external_id).not.toBe(b.external_id);
  });

  it("形の違う入力は 400、置き場に書けなければ 503", async () => {
    const acc = await personal("u-1");
    expect((await put(acc, { kind: "aws_role", role_arn: "x", region: "us-east-1" })).status).toBe(400);
    secrets.fail = true;
    const res = await put(acc, { kind: "anthropic_api_key", api_key: API_KEY });
    expect(res.status).toBe(503);
    expect(res.body.error.code).toBe("credential_unavailable");
  });
});

describe("課題の候補（受け入れ条件 9・10）", () => {
  const ask = (acc: string, exclude?: number[]) =>
    op("postIssueCandidates", "u-1", { accountId: acc, items: [{ repository_id: 101, ...(exclude ? { exclude } : {}) }] }).then((r) => r.body);

  it("順位の表があれば理由つきの候補、選び直しで外した番号は出ない（AC9・AC10）", async () => {
    const acc = await personal("u-1");
    await register(acc);
    h.github.rankings.set("org-a/repo-a", { milestone: "03 Factory", rows: [{ number: 5, title: "e" }, { number: 6, title: "f" }] });
    const res = await ask(acc);
    expect(res).toMatchObject({ source: "ranking", milestone: "03 Factory", note: null });
    expect(res.candidates.every((c: { reason: string }) => c.reason !== "")).toBe(true);
    const again = await ask(acc, [5]);
    expect(again.candidates.map((c: { number: number }) => c.number)).toEqual([6]);
  });

  it("表が無ければ新しい順、Bedrock が失敗すると note を出し順のまま理由を付ける（AC9）", async () => {
    const acc = await personal("u-1");
    await register(acc);
    h.github.openIssues.set("org-a/repo-a", [{ number: 9, title: "i" }, { number: 8, title: "h" }]);
    model.fn = () => Promise.reject(new Error("ThrottlingException"));
    const res = await ask(acc);
    expect(res.source).toBe("recent");
    expect(res.note).toBe(CANDIDATE_FALLBACK_NOTE);
    expect(res.candidates.map((c: { number: number }) => c.number)).toEqual([9, 8]);
  });
});

describe("Factory のオーガナイザーの経路（#2439 の AC 9・11、I66）", () => {
  it("一覧と履歴は GET で読め、止まっていない配布を解く操作は 404、承認の依頼へ答える経路は無い", async () => {
    const acc = await personal("u-1");
    await register(acc);
    const list = await op("listRepositoryApprovalRequests", "u-1", { accountId: acc, query: { repository_id: [101] } });
    expect(list).toEqual({ status: 200, body: { repository_id: 101, organizer: { current_seq: null, wait_reason: null, wait_detail: null }, items: [] } });
    const history = await call("GET", `/accounts/${acc}/releases?repository_id=101&limit=5`, "u-1");
    expect(await history.json()).toEqual([]);
    expect((await call("GET", `/accounts/${acc}/releases?repository_id=101&limit=0`, "u-1")).status).toBe(400);
    const resolve = (choice: string) => op("resolveStalledRelease", "u-1", { accountId: acc, items: [{ repository_id: 101, seq: 1, choice }] });
    expect((await resolve("reject")).status).toBe(404);
    expect((await resolve("maybe")).status).toBe(400);
    expect([404, 405]).toContain((await call("POST", `/accounts/${acc}/approval-requests`, "u-1", [{}])).status);
  });

  it("他人のアカウントのリポジトリは 403", async () => {
    const acc = await personal("u-1");
    await register(acc);
    await personal("u-2");
    expect((await op("listRepositoryApprovalRequests", "u-2", { accountId: acc, query: { repository_id: [101] } })).status).toBe(403);
  });
});
