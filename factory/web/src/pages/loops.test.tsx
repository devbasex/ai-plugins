import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { App } from "../App.tsx";
import { ApiFailure } from "../api.ts";
import { createDummyServices } from "../dummy/index.ts";
import { PERSONAL_ID } from "../dummy/data.ts";
import type { LocaleSettings } from "../i18n/settings.ts";

afterEach(cleanup);

const tokyo: LocaleSettings = { language: "ja", locale: "ja-JP", timeZone: "Asia/Tokyo" };

function Where() {
  const l = useLocation();
  return <output data-testid="where">{l.pathname}</output>;
}

function show(path: string, search = "") {
  const services = createDummyServices(search);
  render(
    <MemoryRouter initialEntries={[path]}>
      <App services={services} settings={tokyo} />
      <Where />
    </MemoryRouter>,
  );
  return services.api;
}

const loops = `/accounts/${PERSONAL_ID}/repositories/101/loops`;
const detail = (id: string) => `/accounts/${PERSONAL_ID}/loops/${id}`;

describe("ループの一覧（受け入れ条件 11）", () => {
  it("名前・選定の対象・状態・周回の数・費用・予算の残り・最後に動いた時刻・止まった理由を並べ、行を押すと詳細へ移る", async () => {
    show(loops);
    const table = await screen.findByRole("table", { name: "開発ループ" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell")[0]!.textContent)).toEqual(["lfactory", "lroot", "lflow"]);
    expect(within(rows[0]!).getByText("8 周 / $87.50 / 時間はリポジトリの既定")).toBeTruthy();
    expect(within(rows[1]!).getByText("周回の数・費用・時間はリポジトリの既定")).toBeTruthy();
    expect(within(table).queryByText(/掛けない/)).toBeNull();
    expect(within(rows[0]!).getByText("2026/11/2 8:30:00")).toBeTruthy();
    expect(within(rows[1]!).getByText("次の周回を始める操作を待っています")).toBeTruthy();
    expect(within(rows[2]!).getByText(/費用の予算に達した.*新しいループで続けられます/)).toBeTruthy();
    await userEvent.click(within(rows[0]!).getByRole("link"));
    expect(screen.getByTestId("where").textContent).toBe(detail("lp-0001"));
  });

  it("0 件なら空の状態と始める欄を出す", async () => {
    show(loops, "?scenario=empty");
    expect(await screen.findByText("まだループがありません")).toBeTruthy();
    expect(screen.getByRole("button", { name: "ループを始める" })).toBeTruthy();
  });
});

describe("ループを始める", () => {
  it("名前とマイルストーンの誤りは送らずに欄に出す", async () => {
    const api = show(loops);
    const start = vi.spyOn(api, "startLoop");
    await screen.findByRole("table");
    await userEvent.type(screen.getByRole("textbox", { name: "名前" }), "bad name");
    await userEvent.type(screen.getByRole("textbox", { name: "費用の予算（USD）" }), "0");
    await userEvent.click(screen.getByRole("button", { name: "ループを始める" }));
    expect(screen.getByText("英数字と - _ で 64 字までにしてください")).toBeTruthy();
    expect(screen.getByText("マイルストーンを 1 つ以上選んでください")).toBeTruthy();
    expect(screen.getByText("0 より大きい数を入れてください")).toBeTruthy();
    expect(start).not.toHaveBeenCalled();
  });

  it("重なるマイルストーンで断られると、題と相手のループの名前を出す", async () => {
    const api = show(loops);
    vi.spyOn(api, "startLoop").mockRejectedValue(new ApiFailure(409, "milestone_in_use", "", { milestones: ["01 見本"], loop_name: "lfactory" }));
    await screen.findByRole("table");
    await userEvent.type(screen.getByRole("textbox", { name: "名前" }), "lnew");
    await userEvent.type(screen.getByRole("textbox", { name: "選定の対象のマイルストーン" }), "01 見本");
    await userEvent.click(screen.getByRole("button", { name: "ループを始める" }));
    expect((await screen.findByRole("alert")).textContent).toBe("01 見本 はループ lfactory が選んでいます");
  });

  it("始めると入力のとおりに送り、詳細へ移る", async () => {
    const api = show(loops);
    const start = vi.spyOn(api, "startLoop");
    await screen.findByRole("table");
    await userEvent.type(screen.getByRole("textbox", { name: "名前" }), "lnew");
    await userEvent.type(screen.getByRole("textbox", { name: "選定の対象のマイルストーン" }), "02 a{enter}03 b");
    await userEvent.type(screen.getByRole("textbox", { name: "周回の数の予算" }), "3");
    await userEvent.click(screen.getByRole("button", { name: "ループを始める" }));
    expect(start).toHaveBeenCalledWith(PERSONAL_ID, 101, { name: "lnew", milestones: ["02 a", "03 b"], budget: { laps: 3, usd: null, hours: null } });
    expect(await screen.findByText("このループは見つかりません")).toBeTruthy();
    expect(screen.getByTestId("where").textContent).toBe(detail("lp-new"));
  });
});

describe("ループの詳細（受け入れ条件 12）", () => {
  it("周回・実行・利用上限の再開の時刻を出し、人の判断待ちに答えると答えの API を呼ぶ", async () => {
    const api = show(detail("lp-0001"));
    const answer = vi.spyOn(api, "answerLoop");
    expect(await screen.findByRole("heading", { level: 1, name: "lfactory" })).toBeTruthy();
    expect(screen.getByText(/利用上限のため 2026\/11\/2 12:00:00 に再開します/)).toBeTruthy();
    const laps = screen.getByRole("table", { name: "周回" });
    expect(within(laps).getByText("#3 #4 #5")).toBeTruthy();
    expect(within(laps).getByText("1.2.0")).toBeTruthy();
    const pending = screen.getByRole("region", { name: "人の判断待ち" });
    expect(within(pending).getByText("#22（周回 2）: MVV 判定が「従う」以外")).toBeTruthy();
    await userEvent.type(within(pending).getByRole("textbox"), "2380");
    await userEvent.click(within(pending).getByRole("button", { name: "承認する" }));
    expect(answer).toHaveBeenCalledWith(PERSONAL_ID, "lp-0001", 22, "approved", 2380);
  });

  it("オフラインでは答え・止める・予算の保存を押せない", async () => {
    show(detail("lp-0001"), "?connection=offline");
    await screen.findByRole("heading", { level: 1, name: "lfactory" });
    expect((screen.getByRole("button", { name: "承認する" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "ループを止める" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "予算を保存する" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("次の周回を始める操作を待つループは、ボタンで次の周回を求める。人の判断待ちが無ければ節を出さない", async () => {
    const api = show(detail("lp-0002"));
    const next = vi.spyOn(api, "requestNextLap");
    await userEvent.click(await screen.findByRole("button", { name: "次の周回を始める" }));
    expect(next).toHaveBeenCalledWith(PERSONAL_ID, "lp-0002");
    expect(screen.queryByRole("region", { name: "人の判断待ち" })).toBeNull();
  });

  it("予算を変えると送り、止まったループには操作を出さない", async () => {
    const api = show(detail("lp-0001"));
    const update = vi.spyOn(api, "updateLoopBudget");
    const laps = await screen.findByRole("textbox", { name: "周回の数の予算" });
    expect((laps as HTMLInputElement).value).toBe("10");
    await userEvent.clear(laps);
    await userEvent.type(laps, "12");
    await userEvent.click(screen.getByRole("button", { name: "予算を保存する" }));
    expect(update).toHaveBeenCalledWith(PERSONAL_ID, "lp-0001", { laps: 12, usd: 100, hours: null });
    expect((laps as HTMLInputElement).placeholder).toBe("空ならリポジトリの既定");
    cleanup();
    show(detail("lp-0003"));
    await screen.findByRole("heading", { level: 1, name: "lflow" });
    expect(screen.queryByRole("button", { name: "ループを止める" })).toBeNull();
  });
});

describe("予算をリポジトリの既定へ戻す（#2406）", () => {
  it("既定へ戻せない版で断られると、版を差し込んだ理由を予算の欄に出す", async () => {
    const api = show(detail("lp-0001"));
    vi.spyOn(api, "updateLoopBudget").mockRejectedValue(new ApiFailure(409, "budget_reset_unsupported", "", { ndf_version: "10.26.1", units: ["usd"] }));
    await userEvent.clear(await screen.findByRole("textbox", { name: "費用の予算（USD）" }));
    await userEvent.click(screen.getByRole("button", { name: "予算を保存する" }));
    expect(await screen.findByText(/NDF（版 10.26.1）は予算をリポジトリの既定へ戻せません/)).toBeTruthy();
  });
});

describe("リポジトリの同時の実行の数（受け入れ条件 8）", () => {
  it("持ち主は宣言でき、範囲の外は送らない。行からループの画面へ移れる", async () => {
    const api = show(`/accounts/${PERSONAL_ID}/repositories`);
    const update = vi.spyOn(api, "updateRepository");
    const cards = await screen.findByRole("list", { name: "登録したリポジトリ" });
    const first = within(cards).getAllByRole("listitem")[0]!;
    const field = within(first).getByRole("textbox", { name: "同時の実行の数（空なら料金プランの値）" });
    await userEvent.type(field, "101");
    await userEvent.click(within(first).getByRole("button", { name: "宣言する" }));
    expect(within(first).getByText("1 から 100 の整数を入れてください")).toBeTruthy();
    expect(update).not.toHaveBeenCalled();
    await userEvent.clear(field);
    await userEvent.type(field, "1");
    await userEvent.click(within(first).getByRole("button", { name: "宣言する" }));
    expect(update).toHaveBeenCalledWith(PERSONAL_ID, 101, 1);
    expect(within(first).getByRole("link", { name: "ループ" }).getAttribute("href")).toBe(loops);
  });
});
