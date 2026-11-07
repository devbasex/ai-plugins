// 承認待ちの一覧の画面（#2430 の受け入れ条件 5・7・8・9・12・14）
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { App } from "../App.tsx";
import { ApiFailure, type Api, type ApprovalRequestRow } from "../api.ts";
import { fixedConnection } from "../connection.ts";
import { createDummyApi, createDummyAuth, createDummyServices } from "../dummy/index.ts";
import { PERSONAL_ID } from "../dummy/data.ts";
import type { LocaleSettings } from "../i18n/settings.ts";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const tokyo: LocaleSettings = { language: "ja", locale: "ja-JP", timeZone: "Asia/Tokyo" };
const online = fixedConnection({ online: true, lastReceivedAt: null, pendingOperations: 0 });
const approvals = `/accounts/${PERSONAL_ID}/approvals`;

function Where() {
  const l = useLocation();
  return <output data-testid="where">{l.pathname}</output>;
}

function show(path: string, api: Api = createDummyApi(), pollIntervalMs?: number) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App services={{ auth: createDummyAuth(), api, connection: online, pollIntervalMs }} settings={tokyo} />
      <Where />
    </MemoryRouter>,
  );
}

const row = (patch: Partial<ApprovalRequestRow>): ApprovalRequestRow => ({
  sprint_run_id: "sr-a",
  repository_id: 101,
  repo: "org-a/repo-a",
  issues: [1],
  kind: "gate1",
  loop: null,
  waiting_since: "2026-11-04T00:00:00Z",
  ...patch,
});

const ROWS: ApprovalRequestRow[] = [
  row({ sprint_run_id: "sr-a", kind: "gate1", loop: { name: "lfactory2", lap: 3 }, issues: [1], waiting_since: "2026-11-04T00:00:01Z" }),
  row({ sprint_run_id: "sr-b", kind: "gate2", loop: { name: "lfactory3", lap: 1 }, issues: [2], repo: "org-a/repo-b", waiting_since: "2026-11-04T00:00:02Z" }),
  row({ sprint_run_id: "sr/c", kind: "decision", loop: null, issues: [3, 4], waiting_since: "2026-11-04T00:00:03Z" }),
];

const apiWith = (rows: ApprovalRequestRow[]) => {
  const api = createDummyApi();
  api.approvalRequests = async () => rows;
  return api;
};

describe("承認待ちの一覧の画面", () => {
  it("行を API の順に、種類・ループ・周回・リポジトリ・課題・待ち始めた日時とともに出し、ループでない行は「—」", async () => {
    show(approvals, apiWith(ROWS));
    const table = await screen.findByRole("table", { name: "承認を待つ依頼" });
    const cells = within(table)
      .getAllByRole("row")
      .slice(1)
      .map((tr) => within(tr).getAllByRole("cell").map((td) => td.textContent));
    expect(cells).toEqual([
      ["設計承認ゲート", "lfactory2", "3", "org-a/repo-a", "#1", "2026/11/4 9:00:01"],
      ["リリース承認ゲート", "lfactory3", "1", "org-a/repo-b", "#2", "2026/11/4 9:00:02"],
      ["判断", "—", "—", "org-a/repo-a", "#3 #4", "2026/11/4 9:00:03"],
    ]);
    // 読むだけで、承認と差し戻しのボタンを置かない
    expect(screen.queryByRole("button", { name: "承認する" })).toBeNull();
    expect(screen.queryByRole("button", { name: "差し戻す" })).toBeNull();
  });

  it("行を押すと、その実行のスプリントの詳細へ移る（実行の ID は符号化する）", async () => {
    show(approvals, apiWith(ROWS));
    const table = await screen.findByRole("table", { name: "承認を待つ依頼" });
    await userEvent.click(within(table).getAllByRole("link")[2]!);
    expect(screen.getByTestId("where").textContent).toBe(`/accounts/${PERSONAL_ID}/sprints/sr%2Fc`);
  });

  it("答えの無い承認の依頼が 0 件なら「承認を待っているものはありません」", async () => {
    show(approvals, apiWith([]));
    expect(await screen.findByText("承認を待っているものはありません")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("読み直しが失敗しても前の行を残し、「読み直せませんでした（<時刻>）」を出す", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = createDummyApi();
    let fail = false;
    api.approvalRequests = async () => {
      if (fail) throw new ApiFailure(503, "unavailable", "x");
      return ROWS;
    };
    show(approvals, api, 1000);
    expect(await screen.findByText("lfactory3")).toBeTruthy();
    fail = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByText("lfactory3")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toMatch(/^読み直せませんでした（/);
  });

  it("403 は「このアカウントを見る権限がありません」", async () => {
    const api = createDummyApi();
    api.approvalRequests = async () => {
      throw new ApiFailure(403, "forbidden", "forbidden");
    };
    show(approvals, api);
    expect(await screen.findByText("このアカウントを見る権限がありません")).toBeTruthy();
  });

  it("ナビゲーションの行き先から届き、ダミーで起動すると見本の行が出る", async () => {
    render(
      <MemoryRouter initialEntries={[`/accounts/${PERSONAL_ID}/sprints`]}>
        <App services={createDummyServices("")} settings={tokyo} />
        <Where />
      </MemoryRouter>,
    );
    const nav = screen.getByRole("navigation", { name: "主な行き先" });
    await userEvent.click(within(nav).getByRole("button", { name: "メニュー" }));
    await userEvent.click(await within(nav).findByRole("link", { name: "承認待ちの一覧" }));
    expect(screen.getByTestId("where").textContent).toBe(approvals);
    const table = await screen.findByRole("table", { name: "承認を待つ依頼" });
    expect(within(table).getByText("lfactory2")).toBeTruthy();
  });
});
