import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { App } from "./App.tsx";
import { createApi, type AccountDetail, type Me } from "./api.ts";
import { createAuth, type Auth } from "./auth.ts";

const config = {
  cognitoDomain: "auth.example.test",
  cognitoClientId: "web-client",
  webUrl: "https://factory.example.test",
  apiUrl: "https://api.example.test",
};

const me: Me = {
  user_id: "11111111-1111-4111-8111-111111111111",
  idp: "github",
  personal_account_id: "p-1",
  accounts: [
    { account_id: "p-1", kind: "personal", name: null, plan: "free", role: "owner", seat_type: null, execution_mode: "local" },
    { account_id: "t-1", kind: "team", name: "開発", plan: "team", role: "member", seat_type: "premium", execution_mode: "managed" },
  ],
};

const personal: AccountDetail = { ...me.accounts[0]!, limits: { concurrent_sprint_runs: 1, monthly_tokens: null }, seats: null };
const team: AccountDetail = {
  ...me.accounts[1]!,
  limits: { concurrent_sprint_runs: 10, monthly_tokens: 125_000_000 },
  seats: [
    { user_id: "owner", idp: "google", seat_type: "standard", added_at: "2026-10-30T00:00:00Z" },
    { user_id: me.user_id, idp: "github", seat_type: "premium", added_at: "2026-10-30T00:00:00Z" },
  ],
};

// 偽の API。経路ごとに応答を返す
function fakeFetch(routes: Record<string, () => Response>): typeof fetch {
  return async (input, init) => {
    const key = `${init?.method ?? "GET"} ${String(input).replace(config.apiUrl, "")}`;
    const route = routes[key];
    return route ? route() : Response.json({ error: { code: "forbidden", message: "x" } }, { status: 403 });
  };
}

let visited: string[];
let auth: Auth;
beforeEach(() => {
  sessionStorage.clear();
  visited = [];
  auth = createAuth(config, sessionStorage, { assign: (url) => visited.push(url) });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function Where() {
  const l = useLocation();
  return <output data-testid="where">{l.pathname + l.search}</output>;
}

function show(path: string, routes: Record<string, () => Response> = {}, a: Auth = auth) {
  const api = createApi(config.apiUrl, a, fakeFetch(routes));
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App services={{ auth: a, api }} />
      <Where />
    </MemoryRouter>,
  );
}

const ok = (body: unknown) => () => Response.json(body);
const where = () => screen.getByTestId("where").textContent;

describe("ログインの画面（受け入れ条件 1・2）", () => {
  it.each([
    ["Google でログイン", "Google"],
    ["GitHub でログイン", "GitHub"],
  ])("「%s」で Cognito へ %s を指定して送る", async (label, idp) => {
    show("/login");
    await userEvent.click(screen.getByRole("button", { name: label }));
    await waitFor(() => expect(visited).toHaveLength(1));
    expect(new URL(visited[0]!).searchParams.get("identity_provider")).toBe(idp);
  });

  it("IdP が拒んだ戻りでは token を保存せず、ログインの画面に「ログインできませんでした」が出る", async () => {
    show("/callback?error=access_denied&state=s");
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "ログインできませんでした");
    expect(where()).toBe("/login?error=access_denied");
    expect(auth.getToken()).toBeNull();
  });

  it("戻りの後に自分のアカウントの画面へ移り、プランが出る", async () => {
    const stub: Auth = { ...auth, completeLogin: async () => (sessionStorage.setItem("ndf-factory.access_token", "t"), { ok: true }) };
    show("/callback?code=c&state=s", { "GET /me": ok(me), "GET /accounts/p-1": ok(personal) }, stub);
    expect(await screen.findByText("プラン: Free")).toBeTruthy();
    expect(where()).toBe("/account");
  });
});

describe("自分のアカウント（受け入れ条件 3・8）", () => {
  beforeEach(() => sessionStorage.setItem("ndf-factory.access_token", "t"));

  it("2 つの上限と実行の形態と、席を持つチームが出る", async () => {
    show("/account", { "GET /me": ok(me), "GET /accounts/p-1": ok(personal) });
    expect(await screen.findByText("同時に動かせるスプリントの実行")).toBeTruthy();
    expect(screen.getByText("1")).toBeTruthy();
    expect(screen.getByText("掛けない")).toBeTruthy();
    expect(screen.getByText("実行の形態: local（手元のランナー）")).toBeTruthy();
    expect(screen.getByRole("link", { name: "開発" })).toBeTruthy();
    expect((screen.getByRole("option", { name: "managed（サービスの Bedrock）" }) as HTMLOptionElement).disabled).toBe(true);
  });

  it("ログアウトで token が消え、自分のアカウントを開くとログインの画面へ移る", async () => {
    show("/account", { "GET /me": ok(me), "GET /accounts/p-1": ok(personal) });
    await userEvent.click(await screen.findByRole("button", { name: "ログアウト" }));
    expect(auth.getToken()).toBeNull();
    expect(visited[0]).toContain("https://auth.example.test/logout");
    cleanup();
    show("/account");
    expect(where()).toBe("/login");
  });

  it("API が 401 を返したら token を消してログインの画面へ移る", async () => {
    show("/account", { "GET /me": () => Response.json({ error: { code: "unauthorized", message: "x" } }, { status: 401 }) });
    await waitFor(() => expect(where()).toBe("/login"));
    expect(auth.getToken()).toBeNull();
  });
});

describe("チームのアカウント（受け入れ条件 8・10・11）", () => {
  beforeEach(() => sessionStorage.setItem("ndf-factory.access_token", "t"));

  it("席を持つ利用者には上限と席の一覧が出て、席の操作と形態の変更は出ない", async () => {
    show("/accounts/t-1", { "GET /accounts/t-1": ok(team) });
    expect(await screen.findByText("125,000,000")).toBeTruthy();
    expect(screen.getByText("10")).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "外す" })).toBeNull();
    expect(screen.queryByRole("button", { name: "席を足す" })).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("持ち主には操作が出て、API の 422 の文を操作の下に出す", async () => {
    show("/accounts/t-1", {
      "GET /accounts/t-1": ok({ ...team, role: "owner" }),
      "POST /accounts/t-1/seats": () =>
        Response.json({ error: { code: "user_not_found", message: "その利用者はまだログインしていません" } }, { status: 422 }),
    });
    await userEvent.type(await screen.findByLabelText("足す利用者の ID"), "22222222-2222-4222-8222-222222222222");
    await userEvent.click(screen.getByRole("button", { name: "席を足す" }));
    expect(await screen.findByText("その利用者はまだログインしていません")).toBeTruthy();
  });

  it("利用者の ID の形でなければ送らない", async () => {
    show("/accounts/t-1", { "GET /accounts/t-1": ok({ ...team, role: "owner" }) });
    await userEvent.type(await screen.findByLabelText("足す利用者の ID"), "octo");
    await userEvent.click(screen.getByRole("button", { name: "席を足す" }));
    expect(await screen.findByText("利用者の ID の形ではありません")).toBeTruthy();
  });

  it("403 ではアカウントの有無を出さない", async () => {
    show("/accounts/nope");
    expect(await screen.findByText("このアカウントを見る権限がありません")).toBeTruthy();
  });
});
