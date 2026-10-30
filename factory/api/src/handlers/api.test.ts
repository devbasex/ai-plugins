import { beforeEach, describe, expect, it } from "vitest";
import { createTestApi, type TestApi } from "../testing/app.ts";

let api: TestApi;
beforeEach(async () => {
  api = await createTestApi();
});

async function me(user: string) {
  return (await (await api.call("GET", "/me", { user })).json()) as {
    user_id: string;
    personal_account_id: string;
    accounts: { account_id: string; kind: string; plan: string; role: string; seat_type: string | null; execution_mode: string }[];
  };
}

async function team(owner: string, seat_type = "standard") {
  await me(owner);
  const res = await api.call("POST", "/teams", { user: owner, body: { name: "チーム", seat_type } });
  expect(res.status).toBe(201);
  return (await res.json()) as { account_id: string; seats: { user_id: string; seat_type: string }[] };
}

const seatsOf = (accountId: string) => api.store.listSeats(accountId);

describe("GET /me（E2、受け入れ条件 12・18、I24）", () => {
  it("初回は Free・local の個人のアカウントを 1 つ作る", async () => {
    const body = await me("u-1");
    expect(body.accounts).toEqual([
      { account_id: body.personal_account_id, kind: "personal", name: null, plan: "free", role: "owner", seat_type: null, execution_mode: "local" },
    ]);
    expect(api.store.history.map((h) => h.type)).toEqual(["account.created"]);
  });

  it("並べて 2 回呼んでも個人のアカウントは 1 つで、同じ ID を返す", async () => {
    const [a, b] = await Promise.all([me("u-1"), me("u-1")]);
    expect(a.personal_account_id).toBe(b.personal_account_id);
    expect([...api.store.accounts.values()].filter((x) => x.ownerUserId === "u-1")).toHaveLength(1);
  });

  it("作れなかったら 503 で、次の要求で作り直す", async () => {
    api.store.beforeWrite = () => {
      throw new Error("down");
    };
    const res = await api.call("GET", "/me", { user: "u-1" });
    expect(res.status).toBe(503);
    expect((await res.json()).error.code).toBe("account_unavailable");
    api.store.beforeWrite = null;
    expect((await api.call("GET", "/me", { user: "u-1" })).status).toBe(200);
  });

  it("席を持つチームが一覧に出る", async () => {
    const t = await team("owner");
    await me("u-1");
    await api.call("POST", `/accounts/${t.account_id}/seats`, { user: "owner", body: { user_id: "u-1", seat_type: "premium" } });
    const body = await me("u-1");
    expect(body.accounts[1]).toMatchObject({ account_id: t.account_id, kind: "team", plan: "team", role: "member", seat_type: "premium" });
  });
});

describe("GET /accounts/{account_id}（受け入れ条件 5・6・8・18）", () => {
  it("個人のアカウントは上限と形態を返し、席を返さない", async () => {
    const { personal_account_id } = await me("u-1");
    const res = await api.call("GET", `/accounts/${personal_account_id}`, { user: "u-1" });
    expect(await res.json()).toEqual({
      account_id: personal_account_id,
      kind: "personal",
      name: null,
      plan: "free",
      role: "owner",
      seat_type: null,
      execution_mode: "local",
      limits: { concurrent_sprint_runs: 1, monthly_tokens: null },
      seats: null,
    });
  });

  it("チームは席の種類で上限が決まり、managed のときだけ月の token を返す", async () => {
    const t = await team("owner", "premium");
    let res = await api.call("GET", `/accounts/${t.account_id}`, { user: "owner" });
    expect((await res.json()).limits).toEqual({ concurrent_sprint_runs: 10, monthly_tokens: null });
    await api.call("PUT", `/accounts/${t.account_id}/execution-mode`, { user: "owner", body: { execution_mode: "managed" } });
    res = await api.call("GET", `/accounts/${t.account_id}`, { user: "owner" });
    const body = await res.json();
    expect(body.limits).toEqual({ concurrent_sprint_runs: 10, monthly_tokens: 125_000_000 });
    expect(body.seats).toEqual([{ user_id: "owner", idp: "google", seat_type: "premium", added_at: expect.any(String) }]);
  });
});

describe("他人のアカウント（受け入れ条件 13、I18）", () => {
  it("他人の個人・入っていないチーム・存在しない ID は、読む・書くのどれも同じ 403", async () => {
    const other = await me("other");
    const t = await team("owner");
    await me("a");
    const targets = [other.personal_account_id, t.account_id, "00000000-0000-4000-8000-999999999999"];
    const bodies = new Set<string>();
    for (const id of targets) {
      for (const [method, path, body] of [
        ["GET", `/accounts/${id}`, undefined],
        ["PUT", `/accounts/${id}/execution-mode`, { execution_mode: "byok" }],
        ["POST", `/accounts/${id}/seats`, { user_id: "a", seat_type: "standard" }],
        ["PATCH", `/accounts/${id}/seats/owner`, { seat_type: "premium" }],
        ["DELETE", `/accounts/${id}/seats/owner`, undefined],
      ] as const) {
        const res = await api.call(method, path, { user: "a", body });
        expect(res.status, `${method} ${path}`).toBe(403);
        bodies.add(await res.text());
      }
    }
    expect(bodies.size).toBe(1);
    expect(await seatsOf(t.account_id)).toHaveLength(1);
  });
});

describe("チームと席（受け入れ条件 9〜11、I13〜I17・I20）", () => {
  it("チームを作ると持ち主の席が選んだ種類で 1 つでき、形態は local", async () => {
    const t = await team("owner", "premium");
    expect(t).toMatchObject({ kind: "team", plan: "team", role: "owner", seat_type: "premium", execution_mode: "local" });
    expect(t.seats).toEqual([expect.objectContaining({ user_id: "owner", seat_type: "premium" })]);
  });

  it.each([
    [{ name: "", seat_type: "standard" }],
    [{ name: "あ".repeat(65), seat_type: "standard" }],
    [{ name: "x", seat_type: "gold" }],
  ])("名前か席の種類が違えば 400: %j", async (body) => {
    await me("owner");
    expect((await api.call("POST", "/teams", { user: "owner", body })).status).toBe(400);
  });

  it("持ち主は席を足し、種類を変え、外せる。そのたびに履歴が 1 つ増える", async () => {
    const t = await team("owner");
    await me("u-1");
    const before = api.store.history.length;
    let res = await api.call("POST", `/accounts/${t.account_id}/seats`, { user: "owner", body: { user_id: "u-1", seat_type: "standard" } });
    expect(res.status).toBe(201);
    res = await api.call("PATCH", `/accounts/${t.account_id}/seats/u-1`, { user: "owner", body: { seat_type: "premium" } });
    expect(await res.json()).toMatchObject({ user_id: "u-1", seat_type: "premium" });
    res = await api.call("DELETE", `/accounts/${t.account_id}/seats/u-1`, { user: "owner" });
    expect(res.status).toBe(204);
    expect(api.store.history.slice(before).map((h) => h.type)).toEqual(["seat.added", "seat.changed", "seat.removed"]);
    expect(await seatsOf(t.account_id)).toHaveLength(1);
  });

  it("持ち主でない席の利用者の 3 操作は 403 で、席は変わらない", async () => {
    const t = await team("owner");
    await me("member");
    await me("u-2");
    await api.call("POST", `/accounts/${t.account_id}/seats`, { user: "owner", body: { user_id: "member", seat_type: "standard" } });
    const seats = await seatsOf(t.account_id);
    const before = api.store.history.length;
    for (const [method, path, body] of [
      ["POST", `/accounts/${t.account_id}/seats`, { user_id: "u-2", seat_type: "standard" }],
      ["PATCH", `/accounts/${t.account_id}/seats/owner`, { seat_type: "premium" }],
      ["DELETE", `/accounts/${t.account_id}/seats/owner`, undefined],
      ["PATCH", `/accounts/${t.account_id}/seats/nobody`, { seat_type: "premium" }],
    ] as const) {
      expect((await api.call(method, path, { user: "member", body })).status).toBe(403);
    }
    expect(await seatsOf(t.account_id)).toEqual(seats);
    expect(api.store.history.length).toBe(before);
  });

  it("未ログイン → 422、既に席がある → 409、持ち主の席を外す → 409。席も履歴も変わらない", async () => {
    const t = await team("owner");
    await me("u-1");
    await api.call("POST", `/accounts/${t.account_id}/seats`, { user: "owner", body: { user_id: "u-1", seat_type: "standard" } });
    const seats = await seatsOf(t.account_id);
    const before = api.store.history.length;
    const cases = [
      ["POST", `/accounts/${t.account_id}/seats`, { user_id: "never", seat_type: "standard" }, 422, "user_not_found"],
      ["POST", `/accounts/${t.account_id}/seats`, { user_id: "u-1", seat_type: "premium" }, 409, "seat_exists"],
      ["DELETE", `/accounts/${t.account_id}/seats/owner`, undefined, 409, "owner_seat_protected"],
      ["PATCH", `/accounts/${t.account_id}/seats/nobody`, { seat_type: "premium" }, 404, "seat_not_found"],
      ["DELETE", `/accounts/${t.account_id}/seats/nobody`, undefined, 404, "seat_not_found"],
    ] as const;
    for (const [method, path, body, status, code] of cases) {
      const res = await api.call(method, path, { user: "owner", body });
      expect(res.status, code).toBe(status);
      expect((await res.json()).error).toEqual({ code, message: expect.any(String) });
    }
    expect(await seatsOf(t.account_id)).toEqual(seats);
    expect(api.store.history.length).toBe(before);
  });

  it("個人のアカウントへの席の操作は 409 not_a_team", async () => {
    const { personal_account_id } = await me("u-1");
    await me("u-2");
    const res = await api.call("POST", `/accounts/${personal_account_id}/seats`, { user: "u-1", body: { user_id: "u-2", seat_type: "standard" } });
    expect(res.status).toBe(409);
    expect((await res.json()).error.code).toBe("not_a_team");
  });

  it("書き込みが失敗したら席も履歴も増えない", async () => {
    const t = await team("owner");
    await me("u-1");
    const before = api.store.history.length;
    api.store.beforeWrite = () => {
      throw new Error("down");
    };
    const res = await api.call("POST", `/accounts/${t.account_id}/seats`, { user: "owner", body: { user_id: "u-1", seat_type: "standard" } });
    expect(res.status).toBe(503);
    expect(api.store.history.length).toBe(before);
    expect(await seatsOf(t.account_id)).toHaveLength(1);
  });
});

describe("実行の形態（受け入れ条件 18・19、I21・I22）", () => {
  const put = (user: string, id: string, execution_mode: unknown) =>
    api.call("PUT", `/accounts/${id}/execution-mode`, { user, body: { execution_mode } });

  it("持ち主は形態を変えられ、履歴が 1 つ増える。同じ値なら増えない", async () => {
    const { personal_account_id: id } = await me("u-1");
    const before = api.store.history.length;
    const res = await put("u-1", id, "byok");
    expect(res.status).toBe(200);
    expect((await res.json()).execution_mode).toBe("byok");
    expect((await put("u-1", id, "byok")).status).toBe(200);
    expect(api.store.history.slice(before).map((h) => [h.type, h.before, h.after])).toEqual([["execution_mode.changed", "local", "byok"]]);
  });

  it("Free で managed は 409 と理由の文で、形態も履歴も変わらない", async () => {
    const { personal_account_id: id } = await me("u-1");
    const before = api.store.history.length;
    const res = await put("u-1", id, "managed");
    expect(res.status).toBe(409);
    expect((await res.json()).error).toEqual({
      code: "managed_not_allowed_on_free",
      message: "Free のプランでは managed（サービスの Bedrock）を選べません",
    });
    expect(api.store.accounts.get(id)?.executionMode).toBe("local");
    expect(api.store.history.length).toBe(before);
  });

  it("Pro なら managed を選べる", async () => {
    const { personal_account_id: id } = await me("u-1");
    await api.service.setPlan(id, "pro");
    expect((await (await put("u-1", id, "managed")).json()).limits).toEqual({ concurrent_sprint_runs: 3, monthly_tokens: 20_000_000 });
  });

  it("チームの持ち主でない席の利用者は 403 で、形態は変わらない", async () => {
    const t = await team("owner");
    await me("member");
    await api.call("POST", `/accounts/${t.account_id}/seats`, { user: "owner", body: { user_id: "member", seat_type: "standard" } });
    const res = await put("member", t.account_id, "byok");
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({ error: { code: "forbidden", message: "このアカウントを見る権限がありません" } });
    expect(api.store.accounts.get(t.account_id)?.executionMode).toBe("local");
  });

  it("知らない値は 400", async () => {
    const { personal_account_id: id } = await me("u-1");
    expect((await put("u-1", id, "cloud")).status).toBe(400);
  });
});
