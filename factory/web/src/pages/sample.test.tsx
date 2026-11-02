import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { App } from "../App.tsx";
import { createDummyServices } from "../dummy/index.ts";
import { PERSONAL_ID } from "../dummy/data.ts";
import type { LocaleSettings } from "../i18n/settings.ts";

afterEach(cleanup);

const tokyo: LocaleSettings = { language: "ja", locale: "ja-JP", timeZone: "Asia/Tokyo" };

function Where() {
  const l = useLocation();
  return <output data-testid="where">{l.pathname}</output>;
}

function show(path: string, search = "", extra: Partial<Parameters<typeof App>[0]> = {}) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App services={createDummyServices(search)} settings={tokyo} {...extra} />
      <Where />
    </MemoryRouter>,
  );
}

const sprints = `/accounts/${PERSONAL_ID}/sprints`;

describe("見本の画面（受け入れ条件 7・8）", () => {
  it("ダミーの一覧に 3 件が新しい順に出て、1 行を押すとその詳細へ移る", async () => {
    show(sprints);
    const table = await screen.findByRole("table", { name: "スプリントの実行" });
    const links = within(table).getAllByRole("link");
    expect(links.map((a) => a.textContent)).toEqual(["#12 #15", "#8", "#3 #4 #5"]);
    expect(within(table).getByText("承認待ち")).toBeTruthy();
    expect(within(table).getByText("2026/11/2 8:30:00")).toBeTruthy();
    await userEvent.click(links[1]!);
    expect(screen.getByTestId("where").textContent).toBe(`${sprints}/sr-0002`);
    expect(await screen.findByText("timeout")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("#8");
    expect(screen.getByText("example-org/a-repository-with-a-very-long-name-for-narrow-screens")).toBeTruthy();
  });

  it("課題の番号で絞り込む", async () => {
    show(sprints);
    await screen.findByRole("table");
    await userEvent.type(screen.getByRole("searchbox", { name: "課題の番号で絞る" }), "#4");
    expect(within(screen.getByRole("table")).getAllByRole("link").map((a) => a.textContent)).toEqual(["#3 #4 #5"]);
    await userEvent.clear(screen.getByRole("searchbox"));
    await userEvent.type(screen.getByRole("searchbox"), "999");
    expect(screen.getByText("絞り込みに合う実行はありません")).toBeTruthy();
  });

  it("ダミーが 0 件を返すと空の状態を出す", async () => {
    show(sprints, "?scenario=empty");
    expect(await screen.findByText("スプリントの実行はまだありません")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("ダミーが失敗を返すと失敗の表示とやり直しのボタンを出す", async () => {
    show(sprints, "?scenario=error");
    expect((await screen.findByRole("alert")).textContent).toBe("時間をおいてやり直してください");
    expect(screen.getByRole("button", { name: "もう一度読む" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("スプリントの一覧");
  });

  it("見つからない実行の詳細は空の状態を出す", async () => {
    show(`${sprints}/sr-none`);
    expect(await screen.findByText("このスプリントの実行は見つかりません")).toBeTruthy();
  });
});

describe("枠とつながりの状態（受け入れ条件 16）", () => {
  it("ダミーのつながりを offline にすると、見本の画面の枠に最後に受け取った時刻つきで出る", async () => {
    show(sprints, "?connection=offline");
    await screen.findByRole("table");
    expect(screen.getByRole("status", { name: "つながりの状態" }).textContent).toBe("オフライン（最後に受け取った時刻: 2026/11/2 9:00:00）");
  });

  it("確定待ちがあれば数を出す", async () => {
    show(sprints, "?connection=pending");
    expect((await screen.findByRole("status", { name: "つながりの状態" })).textContent).toContain("つながってから確定する操作: 2 件");
  });

  it("個人のアカウントの持ち主には、ナビゲーションにキーの持ち込みまで 4 つの行き先が出る", async () => {
    show(sprints);
    const nav = screen.getByRole("navigation", { name: "主な行き先" });
    expect(await within(nav).findByRole("link", { name: "キーの持ち込み" })).toBeTruthy();
    expect(within(nav).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual([
      "/account",
      sprints,
      `/accounts/${PERSONAL_ID}/repositories`,
      `/accounts/${PERSONAL_ID}/model-credential`,
    ]);
  });

  it("ログインの画面は枠で包まない", () => {
    show("/login");
    expect(screen.queryByRole("navigation")).toBeNull();
  });
});

describe("言語を足す（受け入れ条件 13・I6）", () => {
  it("2 つ目の言語の翻訳のファイルを差し込むと、画面のコードを変えずに見本の画面の文言がその言語で出る", async () => {
    show(sprints, "", {
      settings: { language: "en", locale: "en-US", timeZone: "America/New_York" },
      resources: { en: { translation: { screens: { sprints: "Sprints" }, sprints: { list: "Sprint runs", issues: "Issues" } } } },
    });
    const table = await screen.findByRole("table", { name: "Sprint runs" });
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Sprints");
    expect(within(table).getByRole("columnheader", { name: "Issues" })).toBeTruthy();
    // 訳の無いキーは日本語で出る
    expect(within(table).getByRole("columnheader", { name: "状態" })).toBeTruthy();
    expect(within(table).getByText(/11\/1\/2026/)).toBeTruthy();
  });

  it("ブラウザの言語に翻訳が無ければ日本語で出る", async () => {
    show(sprints, "", { settings: { language: "fr", locale: "fr-FR", timeZone: "Europe/Paris" } });
    expect(await screen.findByRole("table", { name: "スプリントの実行" })).toBeTruthy();
  });
});
