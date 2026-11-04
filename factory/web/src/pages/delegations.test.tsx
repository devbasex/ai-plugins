import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { App } from "../App.tsx";
import { createDummyServices } from "../dummy/index.ts";
import type { LocaleSettings } from "../i18n/settings.ts";

afterEach(cleanup);

const tokyo: LocaleSettings = { language: "ja", locale: "ja-JP", timeZone: "Asia/Tokyo" };

function show(search = "") {
  render(
    <MemoryRouter initialEntries={["/delegations"]}>
      <App services={createDummyServices(search)} settings={tokyo} />
    </MemoryRouter>,
  );
}

describe("エージェントへの委任（#2384）", () => {
  it("発行すると値を 1 度だけ出し、閉じると二度と出さない。一覧の先頭に有効な行が足され、取り消せる", async () => {
    show();
    await userEvent.type(await screen.findByLabelText("クライアントの名前"), "手元の Claude Code");
    await userEvent.click(screen.getByRole("button", { name: "発行する" }));
    const value = (await screen.findByText(/^ndfd_/)).textContent!;
    expect(screen.getByLabelText("MCP のクライアントへの登録の例").textContent).toContain(`Bearer ${value}`);
    await userEvent.click(screen.getByRole("button", { name: "閉じる" }));
    expect(document.body.textContent).not.toContain(value);
    const rows = within(screen.getByRole("table", { name: "発行した資格情報" })).getAllByRole("row").slice(1);
    expect(within(rows[0]!).getByText("有効")).toBeTruthy();
    expect(within(rows[1]!).getByText("期限切れ")).toBeTruthy();
    expect(within(rows[1]!).queryByRole("button", { name: "取り消す" })).toBeNull();
    await userEvent.click(within(rows[0]!).getByRole("button", { name: "取り消す" }));
    expect(await within(rows[0]!).findByText("取り消し済み")).toBeTruthy();
  });

  it("名前が無ければ発行しない", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "発行する" }));
    expect(screen.getByRole("alert").textContent).toBe("名前を入力してください");
  });

  it("オフラインの間は発行のボタンを押せない", async () => {
    show("?connection=offline");
    expect(((await screen.findByRole("button", { name: "発行する" })) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("オフラインの間は発行と取り消しができません")).toBeTruthy();
  });

  it("まだ委任していなければ空の表示", async () => {
    show("?scenario=empty");
    expect(await screen.findByText("まだ委任していません")).toBeTruthy();
  });
});
