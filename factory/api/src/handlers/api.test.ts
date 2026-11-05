import { beforeEach, describe, expect, it } from "vitest";
import { createTestApi, firstResult, type TestApi } from "../testing/app.ts";
import { createApiApp } from "./api.ts";

let api: TestApi;
let keys = 0;
beforeEach(async () => {
  api = await createTestApi();
});

async function me(user: string) {
  return (await (await api.op("getMe", { user })).json())[0] as {
    user_id: string;
    personal_account_id: string;
    accounts: { account_id: string; kind: string; plan: string; role: string; seat_type: string | null; execution_mode: string }[];
  };
}

async function team(owner: string, seat_type = "standard") {
  await me(owner);
  const { status, body } = await firstResult(await api.op("createTeam", { user: owner, items: [{ name: "チーム", seat_type, idempotency_key: `k-${++keys}` }] }));
  expect(status).toBe(200);
  return body as { account_id: string; seats: { user_id: string; seat_type: string }[] };
}

const seatsOf = (accountId: string) => api.store.listSeats(accountId);
const addSeat = (user: string, accountId: string, user_id: string, seat_type: string) =>
  api.op("addSeat", { user, accountId, items: [{ user_id, seat_type }] }).then(firstResult);
const changeSeat = (user: string, accountId: string, user_id: string, seat_type: string) =>
  api.op("changeSeat", { user, accountId, items: [{ user_id, seat_type }] }).then(firstResult);
const removeSeat = (user: string, accountId: string, user_id: string) => api.op("removeSeat", { user, accountId, query: { user_id: [user_id] } }).then(firstResult);
const getAccount = (user: string, id: string) => api.op("getAccount", { user, query: { account_id: [id] } }).then(firstResult);

describe("getMe（E2、受け入れ条件 12・18、I24）", () => {
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
    const res = await api.op("getMe", { user: "u-1" });
    expect(res.status).toBe(503);
    expect((await res.json()).error.code).toBe("account_unavailable");
    api.store.beforeWrite = null;
    expect((await api.op("getMe", { user: "u-1" })).status).toBe(200);
  });

  it("席を持つチームが一覧に出る", async () => {
    const t = await team("owner");
    await me("u-1");
    await addSeat("owner", t.account_id, "u-1", "premium");
    const body = await me("u-1");
    expect(body.accounts[1]).toMatchObject({ account_id: t.account_id, kind: "team", plan: "team", role: "member", seat_type: "premium" });
  });
});

describe("getAccount（受け入れ条件 5・6・8・18）", () => {
  it("個人のアカウントは上限と形態を返し、席を返さない", async () => {
    const { personal_account_id } = await me("u-1");
    expect((await getAccount("u-1", personal_account_id)).body).toEqual({
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
    expect((await getAccount("owner", t.account_id)).body.limits).toEqual({ concurrent_sprint_runs: 10, monthly_tokens: null });
    await api.op("setExecutionMode", { user: "owner", items: [{ account_id: t.account_id, execution_mode: "managed" }] });
    const { body } = await getAccount("owner", t.account_id);
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
      // アカウントそのものの操作は要素の forbidden、アカウントに属するものの操作は要求全体の 403。本文は同じ
      for (const [name, call] of [
        ["getAccount", () => getAccount("a", id)],
        ["setExecutionMode", () => api.op("setExecutionMode", { user: "a", items: [{ account_id: id, execution_mode: "byok" }] }).then(firstResult)],
        ["addSeat", () => addSeat("a", id, "a", "standard")],
        ["changeSeat", () => changeSeat("a", id, "owner", "premium")],
        ["removeSeat", () => removeSeat("a", id, "owner")],
      ] as const) {
        const { status, body } = await call();
        expect(status, name).toBe(403);
        bodies.add(JSON.stringify(body));
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

  it("同じ冪等の鍵の 2 回目は 1 回目のチームを返し、チームは 1 つだけ（#2384 の I14）", async () => {
    await me("owner");
    const create = () => api.op("createTeam", { user: "owner", items: [{ name: "チーム", seat_type: "standard", idempotency_key: "same" }] }).then(firstResult);
    const first = await create();
    const second = await create();
    expect(second.body.account_id).toBe(first.body.account_id);
    expect([...api.store.accounts.values()].filter((a) => a.kind === "team")).toHaveLength(1);
  });

  it.each([
    [{ name: "", seat_type: "standard" }],
    [{ name: "あ".repeat(65), seat_type: "standard" }],
    [{ name: "x", seat_type: "gold" }],
  ])("名前か席の種類が違えば 400: %j", async (body) => {
    await me("owner");
    expect((await firstResult(await api.op("createTeam", { user: "owner", items: [{ ...body, idempotency_key: "k-400" }] }))).status).toBe(400);
  });

  it("持ち主は席を足し、種類を変え、外せる。そのたびに履歴が 1 つ増える", async () => {
    const t = await team("owner");
    await me("u-1");
    const before = api.store.history.length;
    expect((await addSeat("owner", t.account_id, "u-1", "standard")).status).toBe(200);
    expect((await changeSeat("owner", t.account_id, "u-1", "premium")).body).toMatchObject({ user_id: "u-1", seat_type: "premium" });
    expect(await removeSeat("owner", t.account_id, "u-1")).toEqual({ status: 200, body: { removed: true } });
    expect(api.store.history.slice(before).map((h) => h.type)).toEqual(["seat.added", "seat.changed", "seat.removed"]);
    expect(await seatsOf(t.account_id)).toHaveLength(1);
  });

  it("持ち主でない席の利用者の 3 操作は 403 で、席は変わらない", async () => {
    const t = await team("owner");
    await me("member");
    await me("u-2");
    await addSeat("owner", t.account_id, "member", "standard");
    const seats = await seatsOf(t.account_id);
    const before = api.store.history.length;
    for (const result of [
      await addSeat("member", t.account_id, "u-2", "standard"),
      await changeSeat("member", t.account_id, "owner", "premium"),
      await removeSeat("member", t.account_id, "owner"),
      await changeSeat("member", t.account_id, "nobody", "premium"),
    ]) {
      expect(result.status).toBe(403);
    }
    expect(await seatsOf(t.account_id)).toEqual(seats);
    expect(api.store.history.length).toBe(before);
  });

  it("未ログイン → 422、既に席がある → 409、持ち主の席を外す → 409。席も履歴も変わらない", async () => {
    const t = await team("owner");
    await me("u-1");
    await addSeat("owner", t.account_id, "u-1", "standard");
    const seats = await seatsOf(t.account_id);
    const before = api.store.history.length;
    const cases = [
      [() => addSeat("owner", t.account_id, "never", "standard"), 422, "user_not_found"],
      [() => addSeat("owner", t.account_id, "u-1", "premium"), 409, "seat_exists"],
      [() => removeSeat("owner", t.account_id, "owner"), 409, "owner_seat_protected"],
      [() => changeSeat("owner", t.account_id, "nobody", "premium"), 404, "seat_not_found"],
      [() => removeSeat("owner", t.account_id, "nobody"), 404, "seat_not_found"],
    ] as const;
    for (const [call, status, code] of cases) {
      const res = await call();
      expect(res.status, code).toBe(status);
      expect(res.body.error).toEqual({ code, message: expect.any(String) });
    }
    expect(await seatsOf(t.account_id)).toEqual(seats);
    expect(api.store.history.length).toBe(before);
  });

  it("個人のアカウントへの席の操作は 409 not_a_team", async () => {
    const { personal_account_id } = await me("u-1");
    await me("u-2");
    const res = await addSeat("u-1", personal_account_id, "u-2", "standard");
    expect(res.status).toBe(409);
    expect(res.body.error.code).toBe("not_a_team");
  });

  it("書き込みが失敗したら席も履歴も増えない", async () => {
    const t = await team("owner");
    await me("u-1");
    const before = api.store.history.length;
    api.store.beforeWrite = () => {
      throw new Error("down");
    };
    const res = await addSeat("owner", t.account_id, "u-1", "standard");
    expect(res.status).toBe(503);
    expect(api.store.history.length).toBe(before);
    expect(await seatsOf(t.account_id)).toHaveLength(1);
  });
});

describe("実行の形態（受け入れ条件 18・19、I21・I22）", () => {
  const put = (user: string, id: string, execution_mode: unknown) =>
    api.op("setExecutionMode", { user, items: [{ account_id: id, execution_mode }] }).then(firstResult);

  it("持ち主は形態を変えられ、履歴が 1 つ増える。同じ値なら増えない", async () => {
    const { personal_account_id: id } = await me("u-1");
    const before = api.store.history.length;
    const res = await put("u-1", id, "byok");
    expect(res.status).toBe(200);
    expect(res.body.execution_mode).toBe("byok");
    expect((await put("u-1", id, "byok")).status).toBe(200);
    expect(api.store.history.slice(before).map((h) => [h.type, h.before, h.after])).toEqual([["execution_mode.changed", "local", "byok"]]);
  });

  it("Free で managed は 409 と理由の文で、形態も履歴も変わらない", async () => {
    const { personal_account_id: id } = await me("u-1");
    const before = api.store.history.length;
    const res = await put("u-1", id, "managed");
    expect(res.status).toBe(409);
    expect(res.body.error).toEqual({
      code: "managed_not_allowed_on_free",
      message: "Free の料金プランでは managed（サービスの Bedrock）を選べません",
    });
    expect(api.store.accounts.get(id)?.executionMode).toBe("local");
    expect(api.store.history.length).toBe(before);
  });

  it("Pro なら managed を選べる", async () => {
    const { personal_account_id: id } = await me("u-1");
    await api.service.setPlan(id, "pro");
    expect((await put("u-1", id, "managed")).body.limits).toEqual({ concurrent_sprint_runs: 3, monthly_tokens: 20_000_000 });
  });

  it("チームの持ち主でない席の利用者は 403 で、形態は変わらない", async () => {
    const t = await team("owner");
    await me("member");
    await addSeat("owner", t.account_id, "member", "standard");
    const res = await put("member", t.account_id, "byok");
    expect(res.status).toBe(403);
    expect(res.body).toEqual({ error: { code: "forbidden", message: "このアカウントを見る権限がありません" } });
    expect(api.store.accounts.get(t.account_id)?.executionMode).toBe("local");
  });

  it("知らない値は 400", async () => {
    const { personal_account_id: id } = await me("u-1");
    expect((await put("u-1", id, "cloud")).status).toBe(400);
  });
});

describe("CORS（画面と API は別のオリジン）", () => {
  const web = "https://factory.example.com";
  const app = createApiApp(
    () => {
      throw new Error("preflight は依存を使わない");
    },
    () => {},
    () => web,
  );

  it("画面のオリジンの preflight は認証より先に 204 で返す", async () => {
    const res = await app.request("/me", {
      method: "OPTIONS",
      headers: { Origin: web, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization" },
    });
    expect(res.status).toBe(204);
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe(web);
    expect(res.headers.get("Access-Control-Allow-Headers")).toMatch(/Authorization/);
  });

  it("ほかのオリジンには許可の見出しを付けない", async () => {
    const res = await app.request("/me", {
      method: "OPTIONS",
      headers: { Origin: "https://evil.example.com", "Access-Control-Request-Method": "GET" },
    });
    expect(res.headers.get("Access-Control-Allow-Origin")).toBeNull();
  });
});
