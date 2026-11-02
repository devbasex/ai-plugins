import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { I18nextProvider } from "react-i18next";
import { createI18n } from "../i18n/index.ts";
import { LocaleSettingsContext } from "../i18n/settings.ts";
import { ApprovalActions } from "./ApprovalActions.tsx";
import { ConnectionStatus } from "./ConnectionStatus.tsx";
import { DataList } from "./DataList.tsx";
import { ErrorState } from "./ErrorState.tsx";
import { NavMenu } from "./NavMenu.tsx";
import tokens from "./tokens.json";
import { flattenTokens, tokensToCss } from "./tokens.ts";

afterEach(cleanup);

function wrap(children: ReactNode) {
  return render(
    <I18nextProvider i18n={createI18n("ja")}>
      <LocaleSettingsContext.Provider value={{ language: "ja", locale: "ja-JP", timeZone: "Asia/Tokyo" }}>
        <MemoryRouter>{children}</MemoryRouter>
      </LocaleSettingsContext.Provider>
    </I18nextProvider>,
  );
}

describe("つながりの状態（受け入れ条件 16）", () => {
  it.each([
    [{ online: true, lastReceivedAt: null, pendingOperations: 0 }, "オンライン"],
    [{ online: false, lastReceivedAt: "2026-11-02T00:00:00Z", pendingOperations: 0 }, "オフライン（最後に受け取った時刻: 2026/11/2 9:00:00）"],
    [{ online: false, lastReceivedAt: null, pendingOperations: 0 }, "オフライン"],
    [{ online: true, lastReceivedAt: null, pendingOperations: 2 }, "オンラインつながってから確定する操作: 2 件"],
  ])("%o を見分けて出す", (state, text) => {
    wrap(<ConnectionStatus state={state} />);
    expect(screen.getByRole("status").textContent).toBe(text);
  });
});

describe("承認の操作", () => {
  it("オフラインでは押せず「つながってから確定します」を添える", () => {
    const approve = vi.fn();
    wrap(<ApprovalActions online={false} onApprove={approve} onReject={() => {}} />);
    expect((screen.getByRole("button", { name: "承認する" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("つながってから確定します")).toBeTruthy();
  });

  it("オンラインでは押せる", async () => {
    const approve = vi.fn();
    wrap(<ApprovalActions online onApprove={approve} onReject={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "承認する" }));
    expect(approve).toHaveBeenCalledOnce();
    expect(screen.queryByText("つながってから確定します")).toBeNull();
  });
});

describe("一覧と失敗の表示", () => {
  it("rowHref を渡すと 1 列目が詳細へのリンクになる", () => {
    wrap(
      <DataList
        label="見本"
        rows={[{ id: "a", name: "一" }]}
        rowKey={(r) => r.id}
        rowHref={(r) => `/x/${r.id}`}
        columns={[
          { key: "name", header: "名前", render: (r) => r.name },
          { key: "id", header: "ID", render: (r) => r.id },
        ]}
      />,
    );
    expect(screen.getByRole("link", { name: "一" }).getAttribute("href")).toBe("/x/a");
    expect(screen.getByRole("columnheader", { name: "名前" })).toBeTruthy();
  });

  it("失敗の表示はやり直しのボタンを持つ", async () => {
    const retry = vi.fn();
    wrap(<ErrorState message="時間をおいてやり直してください" onRetry={retry} />);
    await userEvent.click(screen.getByRole("button", { name: "もう一度読む" }));
    expect(retry).toHaveBeenCalledOnce();
  });
});

describe("ナビゲーション", () => {
  it("リンクを li に入れず、役割の要る行き先は役割があるときだけ出す", async () => {
    wrap(<NavMenu accountId="p-1" role={null} />);
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
    expect(screen.getAllByRole("link").map((a) => a.textContent)).toEqual(["自分のアカウント", "スプリントの一覧", "リポジトリ"]);
    cleanup();
    wrap(<NavMenu accountId="p-1" role="personal" />);
    expect(screen.getByRole("link", { name: "キーの持ち込み" }).getAttribute("href")).toBe("/accounts/p-1/model-credential");
    const toggle = screen.getByRole("button", { name: "メニュー" });
    await userEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
  });

  it("アカウントが決まらなければ自分のアカウントだけを出す", () => {
    wrap(<NavMenu accountId={null} role={null} />);
    expect(screen.getAllByRole("link").map((a) => a.textContent)).toEqual(["自分のアカウント"]);
  });
});

describe("デザインのトークン（I10）", () => {
  it("入れ子の名前を - でつなぎ、参照と寸法を解く", () => {
    const flat = new Map(flattenTokens(tokens));
    expect(flat.get("--color-primary")).toBe("#1f5fbf");
    expect(flat.get("--color-on-primary")).toBe("#ffffff");
    expect(flat.get("--space-4")).toBe("16px");
    expect(flat.get("--font-size-md")).toBe("1rem");
    expect(flat.get("--font-family")).toContain('"Noto Sans JP"');
    expect(tokensToCss(tokens)).toMatch(/^:root \{\n {2}--color-base: #ffffff;/);
  });

  it("解けない参照は投げる", () => {
    expect(() => flattenTokens({ color: { a: { $value: "{color.missing}" } } })).toThrow("color.missing");
  });
});
