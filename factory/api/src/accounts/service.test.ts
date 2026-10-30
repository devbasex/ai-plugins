import { beforeEach, describe, expect, it } from "vitest";
import { PlanError } from "../errors.ts";
import { MemoryAccountStore } from "./memory-store.ts";
import { AccountService } from "./service.ts";

let store: MemoryAccountStore;
let service: AccountService;
beforeEach(() => {
  store = new MemoryAccountStore();
  let n = 0;
  service = new AccountService({ store, newId: () => `acc-${++n}` });
});

async function personal(userId = "u-1") {
  return (await service.ensureUser({ userId, idp: "google" })).personalAccountId;
}

describe("resolveAccount（受け入れ条件 5）", () => {
  it("経路の ID が無ければ個人、あれば席を持つチームの文脈を返す", async () => {
    const id = await personal();
    expect(await service.resolveAccount({ userId: "u-1", idp: "google" })).toMatchObject({ accountId: id, kind: "personal", role: "owner" });
    const team = await service.createTeam({ userId: "u-1", idp: "google" }, "t", "premium");
    expect(await service.resolveAccount({ userId: "u-1", idp: "google" }, team.accountId)).toMatchObject({
      kind: "team",
      role: "owner",
      seatType: "premium",
      executionMode: "local",
    });
  });

  it("席の無いチームは 403", async () => {
    const team = await service.createTeam({ userId: "owner", idp: "google" }, "t", "standard");
    await expect(service.resolveAccount({ userId: "u-1", idp: "github" }, team.accountId)).rejects.toMatchObject({ status: 403 });
  });
});

describe("setPlan（E6、I13・I20・I21）", () => {
  it("プランを変え、履歴を 1 つ足す", async () => {
    const id = await personal();
    await service.setPlan(id, "max");
    expect(store.accounts.get(id)).toMatchObject({ plan: "max", version: 2 });
    expect(store.history.at(-1)).toMatchObject({ type: "plan.changed", actor: "system", before: "free", after: "max" });
  });

  it("知らないプランと、種類に合わない組は PlanError で、行も履歴も変わらない", async () => {
    const id = await personal();
    const team = await service.createTeam({ userId: "u-1", idp: "google" }, "t", "standard");
    const before = store.history.length;
    await expect(service.setPlan(id, "gold" as never)).rejects.toBeInstanceOf(PlanError);
    await expect(service.setPlan(id, "team")).rejects.toMatchObject({ code: "plan_kind_mismatch" });
    await expect(service.setPlan(team.accountId, "pro")).rejects.toMatchObject({ code: "plan_kind_mismatch" });
    expect(store.accounts.get(id)?.plan).toBe("free");
    expect(store.accounts.get(team.accountId)?.plan).toBe("team");
    expect(store.history.length).toBe(before);
  });

  it("managed の Pro を free へ下げると、同じトランザクションで形態を local にし履歴を 2 つ足す", async () => {
    const id = await personal();
    await service.setPlan(id, "pro");
    const ctx = await service.resolveAccount({ userId: "u-1", idp: "google" });
    await service.setExecutionMode(ctx, "managed");
    const before = store.history.length;
    await service.setPlan(id, "free");
    expect(store.accounts.get(id)).toMatchObject({ plan: "free", executionMode: "local" });
    expect(store.history.slice(before).map((h) => h.type)).toEqual(["plan.changed", "execution_mode.changed"]);
  });

  it("形態の書き込みが失敗すればプランも変わらない", async () => {
    const id = await personal();
    await service.setPlan(id, "pro");
    await service.setExecutionMode(await service.resolveAccount({ userId: "u-1", idp: "google" }), "managed");
    store.beforeWrite = () => {
      throw new Error("down");
    };
    await expect(service.setPlan(id, "free")).rejects.toThrow();
    expect(store.accounts.get(id)).toMatchObject({ plan: "pro", executionMode: "managed" });
  });

  it("読んだ後に別の要求が managed を保存しても、版の条件で読み直し free・local で終わる", async () => {
    const id = await personal();
    await service.setPlan(id, "pro");
    const ctx = await service.resolveAccount({ userId: "u-1", idp: "google" });
    let raced = false;
    store.beforeWrite = async (op) => {
      if (op !== "updateAccount" || raced) return;
      raced = true;
      store.beforeWrite = null;
      await service.setExecutionMode(ctx, "managed");
    };
    await service.setPlan(id, "free");
    expect(raced).toBe(true);
    expect(store.accounts.get(id)).toMatchObject({ plan: "free", executionMode: "local" });
  });

  it("版の競合が 3 回続けば保存の失敗を返す", async () => {
    const id = await personal();
    store.beforeWrite = (op) => {
      if (op === "updateAccount") {
        const a = store.accounts.get(id)!;
        store.accounts.set(id, { ...a, version: a.version + 1 });
      }
    };
    await expect(service.setPlan(id, "pro")).rejects.toMatchObject({ status: 503 });
  });
});

describe("新しいアカウントの行と履歴（現状固定、I24）", () => {
  const at = "2026-01-02T03:04:05.000Z";
  beforeEach(() => {
    let n = 0;
    service = new AccountService({ store, newId: () => `acc-${++n}`, now: () => new Date(at) });
  });

  it("ensureUser は Free・local・版 1 の個人の行と account.created の履歴を作る", async () => {
    const id = await personal();
    expect(store.accounts.get(id)).toStrictEqual({
      accountId: id,
      kind: "personal",
      plan: "free",
      executionMode: "local",
      version: 1,
      ownerUserId: "u-1",
      name: null,
      createdAt: at,
      updatedAt: at,
    });
    const created = store.history.find((h) => h.accountId === id && h.type === "account.created");
    expect(created).toStrictEqual({ accountId: id, type: "account.created", at, actor: "u-1", before: null, after: { kind: "personal", plan: "free", execution_mode: "local" } });
    expect(Object.keys(created!.after as object)).toEqual(["kind", "plan", "execution_mode"]);
  });

  it("createTeam は team・local・版 1 の行と、name を足した account.created の履歴を作る", async () => {
    const team = await service.createTeam({ userId: "u-1", idp: "google" }, "t", "premium");
    expect(store.accounts.get(team.accountId)).toStrictEqual({
      accountId: team.accountId,
      kind: "team",
      plan: "team",
      executionMode: "local",
      version: 1,
      ownerUserId: "u-1",
      name: "t",
      createdAt: at,
      updatedAt: at,
    });
    const history = store.history.filter((h) => h.accountId === team.accountId);
    expect(history.map((h) => h.type)).toEqual(["account.created", "seat.added"]);
    expect(history[0]).toStrictEqual({
      accountId: team.accountId,
      type: "account.created",
      at,
      actor: "u-1",
      before: null,
      after: { kind: "team", plan: "team", execution_mode: "local", name: "t" },
    });
    expect(Object.keys(history[0]!.after as object)).toEqual(["kind", "plan", "execution_mode", "name"]);
  });
});
