// 委任の資格情報と、人だけが確定できる操作（#2384 の受け入れ条件 10〜14・17・18・26、不変条件 I1〜I6・I14）
import { createHash } from "node:crypto";
import { beforeEach, describe, expect, it } from "vitest";
import { OPERATIONS, operationUrl, type OperationId } from "@ndf-factory/contracts/operations";
import { createTestApi, firstResult, type OpOptions, type TestApi } from "../testing/app.ts";

let api: TestApi;
let keys = 0;
beforeEach(async () => {
  api = await createTestApi({ factory: true });
});

async function personal(user: string): Promise<string> {
  return (await (await api.op("getMe", { user })).json())[0].personal_account_id;
}

async function issue(user: string, scope: "read" | "read_write", expires_in_seconds = 3600) {
  const { status, body } = await firstResult(
    await api.op("createDelegation", { user, items: [{ client_name: "手元の Claude Code", scope, expires_in_seconds, idempotency_key: `d-${++keys}` }] }),
  );
  expect(status).toBe(200);
  return body as { delegation: { credential_id: string; expires_at: string }; credential: string };
}

const as = (credential: string) => ({ authorization: `Bearer ${credential}` });
const agent = (credential: string, id: OperationId, opts: OpOptions = {}) => api.op(id, { ...opts, ...as(credential) });

describe("発行（I1・I3・I14）", () => {
  it("値は ndfd_<ID>_<秘密> で 1 度だけ返り、保存した行に平文が無く、ハッシュが値と一致する", async () => {
    await personal("u-1");
    const { delegation, credential } = await issue("u-1", "read");
    const m = /^ndfd_([a-z2-7]{26})_(.+)$/.exec(credential)!;
    expect(m[1]).toBe(delegation.credential_id);
    const row = api.delegationStore.records.get(delegation.credential_id)!;
    expect(JSON.stringify(row)).not.toContain(m[2]);
    expect(row.secretHash).toBe(createHash("sha256").update(m[2]!).digest("hex"));
    const list = await (await api.op("listDelegations", { user: "u-1" })).json();
    expect(list).toEqual([expect.objectContaining({ credential_id: delegation.credential_id, scope: "read", state: "active", revoked_at: null })]);
    expect(JSON.stringify(list)).not.toContain(m[2]);
  });

  it.each([299, 3601])("期限 %i 秒は 400 で保存しない", async (seconds) => {
    await personal("u-1");
    const res = await firstResult(await api.op("createDelegation", { user: "u-1", items: [{ client_name: "x", scope: "read", expires_in_seconds: seconds, idempotency_key: "k" }] }));
    expect(res.status).toBe(400);
    expect(api.delegationStore.records.size).toBe(0);
  });

  it("同じ冪等の鍵の 2 回目は同じ資格情報を返し、値を返さない", async () => {
    await personal("u-1");
    const item = { client_name: "x", scope: "read", expires_in_seconds: 600, idempotency_key: "same" };
    const first = await firstResult(await api.op("createDelegation", { user: "u-1", items: [item] }));
    const second = await firstResult(await api.op("createDelegation", { user: "u-1", items: [item] }));
    expect(second.body.delegation.credential_id).toBe(first.body.delegation.credential_id);
    expect(first.body.credential).toMatch(/^ndfd_/);
    expect(second.body.credential).toBeNull();
    expect(api.delegationStore.records.size).toBe(1);
  });
});

describe("委任の資格情報での認証（受け入れ条件 10〜12、I2・I6）", () => {
  it("人の token と同じ人の委任で、同じ操作の許可と拒否が一致する", async () => {
    const own = await personal("u-1");
    const other = await personal("u-2");
    const { credential } = await issue("u-1", "read_write");
    const cases: [OperationId, OpOptions][] = [
      ["getAccount", { query: { account_id: [own] } }],
      ["getAccount", { query: { account_id: [other] } }],
      ["listSprintRuns", { accountId: own }],
      ["listSprintRuns", { accountId: other }],
      ["setExecutionMode", { items: [{ account_id: own, execution_mode: "byok" }] }],
      ["setExecutionMode", { items: [{ account_id: other, execution_mode: "byok" }] }],
    ];
    for (const [id, opts] of cases) {
      const human = await firstResult(await api.op(id, { ...opts, user: "u-1" }));
      const delegated = await firstResult(await agent(credential, id, opts));
      expect(delegated.status, `${id} ${JSON.stringify(opts)}`).toBe(human.status);
    }
  });

  it("期限を過ぎた資格情報は 401 で、操作を行わない", async () => {
    const own = await personal("u-1");
    const { credential } = await issue("u-1", "read_write", 300);
    expect((await agent(credential, "listSprintRuns", { accountId: own })).status).toBe(200);
    api.clock.now = new Date(api.clock.now.getTime() + 300_000);
    const res = await agent(credential, "setExecutionMode", { items: [{ account_id: own, execution_mode: "byok" }] });
    expect(res.status).toBe(401);
    expect(api.store.accounts.get(own)?.executionMode).toBe("local");
  });

  it("取り消しの後の最初の要求が 401 で、照合の結果をためない", async () => {
    const own = await personal("u-1");
    const { delegation, credential } = await issue("u-1", "read");
    const reads = api.delegationStore.reads;
    expect((await agent(credential, "listSprintRuns", { accountId: own })).status).toBe(200);
    expect((await agent(credential, "listSprintRuns", { accountId: own })).status).toBe(200);
    expect(api.delegationStore.reads - reads).toBe(2);
    const revoked = await firstResult(await api.op("revokeDelegation", { user: "u-1", query: { credential_id: [delegation.credential_id] } }));
    expect(revoked.body.state).toBe("revoked");
    expect((await agent(credential, "listSprintRuns", { accountId: own })).status).toBe(401);
  });

  it("形の違う値・秘密の違う値・無い ID は 401", async () => {
    await personal("u-1");
    const { credential } = await issue("u-1", "read");
    const wrongSecret = credential.slice(0, -1) + (credential.endsWith("A") ? "B" : "A");
    for (const value of ["ndfd_x", wrongSecret, `ndfd_${"a".repeat(26)}_${"b".repeat(43)}`]) {
      expect((await api.op("getMe", as(value))).status, value).toBe(401);
    }
  });

  it("委任を使わない人の操作は、今と同じく token の検証の失敗が 401、アカウントの解決の失敗が 403（受け入れ条件 26）", async () => {
    expect((await api.op("getMe", { authorization: "Bearer broken" })).status).toBe(401);
    await personal("u-1");
    expect((await api.op("listSprintRuns", { user: "u-1", accountId: "00000000-0000-4000-8000-0000000000ff" })).status).toBe(403);
  });
});

describe("範囲と人だけが確定できる操作（受け入れ条件 13・14・16〜18、I4・I5）", () => {
  it("読み取りだけの範囲で書き込みの操作は 403 scope_insufficient で、操作を行わない", async () => {
    const own = await personal("u-1");
    const { credential } = await issue("u-1", "read");
    const res = await agent(credential, "setExecutionMode", { items: [{ account_id: own, execution_mode: "byok" }] });
    expect(res.status).toBe(403);
    expect((await res.json()).error.code).toBe("scope_insufficient");
    expect(api.store.accounts.get(own)?.executionMode).toBe("local");
  });

  it("委任の資格情報で委任の資格情報の発行を呼ぶと 403", async () => {
    await personal("u-1");
    const { credential } = await issue("u-1", "read_write");
    const res = await agent(credential, "createDelegation", { items: [{ client_name: "y", scope: "read_write", expires_in_seconds: 600, idempotency_key: "z" }] });
    expect(res.status).toBe(403);
    expect((await res.json()).error.code).toBe("human_only");
    expect(api.delegationStore.records.size).toBe(1);
  });

  it("扱い human の操作をすべて読み書きの範囲で呼ぶと 403 human_only で、何も保存されない", async () => {
    const own = await personal("u-1");
    const { delegation, credential } = await issue("u-1", "read_write");
    const snapshot = () =>
      JSON.stringify([
        [...api.store.accounts.values()],
        [...api.store.seats.values()],
        api.store.history.length,
        [...api.delegationStore.records.values()],
        [...api.h!.table.loops.values()],
        [...api.h!.table.credentials.values()],
        [...api.secrets.values.values()],
      ]);
    const before = snapshot();
    const human = OPERATIONS.filter((op) => op.access === "human");
    expect(human).toHaveLength(10);
    for (const op of human) {
      const byQuery = ["select", "delete"].includes(op.kind);
      const query = Object.fromEntries(op.keys.length && byQuery ? [[op.keys[0]!, ["x"]]] : []);
      if (op.operationId === "revokeDelegation") query.credential_id = [delegation.credential_id];
      const item = { user_id: "u-2", seat_type: "premium", name: "t", kind: "anthropic_api_key", api_key: "sk-SECRET", loop_id: "l", issue: 1, decision: "approved", repository_id: 1, seq: 1, choice: "reject", client_name: "c", scope: "read", expires_in_seconds: 600, idempotency_key: "h" };
      const hasBody = !byQuery;
      const res = await api.call(op.method, operationUrl(op, own, query), { ...as(credential), body: hasBody ? [item] : undefined });
      expect(res.status, op.operationId).toBe(403);
      expect((await res.json()).error.code, op.operationId).toBe("human_only");
    }
    expect(snapshot()).toBe(before);
  });

  it("委任の資格情報で、スプリントの詳細と承認待ちの一覧は読める", async () => {
    const own = await personal("u-1");
    const { credential } = await issue("u-1", "read");
    const h = api.h!;
    h.table.registrations.set(`${own}#101`, { accountId: own, repositoryId: 101, fullName: "o/r", installationId: 9, registeredAt: "2026-11-04T00:00:00.000Z" } as never);
    const list = await firstResult(await agent(credential, "listRepositoryApprovalRequests", { accountId: own, query: { repository_id: [101] } }));
    expect(list.status).toBe(200);
    expect(list.body.items).toEqual([]);
    const detail = await agent(credential, "getSprintRunDetail", { accountId: own, query: { sprint_run_id: ["sr-none"] } });
    expect(detail.status).toBe(200);
    expect((await detail.json())[0].error.code).toBe("forbidden");
  });
});

describe("取り消し（E9）", () => {
  it("他人の資格情報は forbidden、期限切れの資格情報は何も変えずに成功", async () => {
    await personal("u-1");
    await personal("u-2");
    const { delegation } = await issue("u-1", "read", 300);
    const others = await firstResult(await api.op("revokeDelegation", { user: "u-2", query: { credential_id: [delegation.credential_id] } }));
    expect(others.status).toBe(403);
    api.clock.now = new Date(api.clock.now.getTime() + 301_000);
    const expired = await firstResult(await api.op("revokeDelegation", { user: "u-1", query: { credential_id: [delegation.credential_id] } }));
    expect(expired.body).toMatchObject({ state: "expired", revoked_at: null });
    expect(api.delegationStore.records.get(delegation.credential_id)?.revokedAt).toBeNull();
  });
});
