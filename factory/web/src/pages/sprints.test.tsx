import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { App } from "../App.tsx";
import { ApiFailure, createApi, type Api, type SprintRunRow, type StepDetail } from "../api.ts";
import type { Auth } from "../auth.ts";
import { fixedConnection } from "../connection.ts";
import { createDummyApi, createDummyAuth } from "../dummy/index.ts";
import { PERSONAL_ID, sprintRuns } from "../dummy/data.ts";
import type { LocaleSettings } from "../i18n/settings.ts";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const tokyo: LocaleSettings = { language: "ja", locale: "ja-JP", timeZone: "Asia/Tokyo" };
const online = fixedConnection({ online: true, lastReceivedAt: null, pendingOperations: 0 });
const sprints = `/accounts/${PERSONAL_ID}/sprints`;

function Where() {
  const l = useLocation();
  return <output data-testid="where">{l.pathname}</output>;
}

function show(path: string, api: Api = createDummyApi(), auth: Auth = createDummyAuth(), pollIntervalMs?: number) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App services={{ auth, api, connection: online, pollIntervalMs }} settings={tokyo} />
      <Where />
    </MemoryRouter>,
  );
}

describe("スプリントの一覧（受け入れ条件 1〜3）", () => {
  it("各行に今のフェーズと今の工程を出し、phase.changed の無い実行は「始まる前」、5 つに無い名前は「その他（<名前>）」", async () => {
    const api = createDummyApi();
    const rows: SprintRunRow[] = [
      { ...sprintRuns[0]!, current_phase: "設計" },
      { ...sprintRuns[1]!, sprint_run_id: "sr-x", current_phase: "検査", created_at: "2026-10-31T11:00:00Z" },
      { ...sprintRuns[2]!, current_phase: "仕上げ" },
      { ...sprintRuns[1]!, current_phase: null },
      { ...sprintRuns[1]!, sprint_run_id: "sr-y", current_phase: "準備", created_at: "2026-10-29T00:00:00Z" },
    ];
    api.sprintRuns = async () => ({ sprint_runs: rows });
    show(sprints, api);
    const table = await screen.findByRole("table", { name: "スプリントの実行" });
    const phases = within(table)
      .getAllByRole("row")
      .slice(1)
      .map((r) => r.querySelector("[data-label='フェーズ'] .phase-text")?.textContent);
    expect(phases).toEqual(["設計", "検査", "始まる前", "仕上げ", "その他（準備）"]);
    expect(within(table).getAllByText("今の工程: impl")).toHaveLength(1);
    expect(within(table).getByRole("img", { name: "検査（今）" })).toBeTruthy();
  });

  it("件数の札と、状態のタブ（動いている・対応待ち・終わった）で画面の中で絞る", async () => {
    show(sprints);
    const table = await screen.findByRole("table");
    const summary = within(screen.getByLabelText("件数")).getAllByRole("definition");
    expect(summary.map((d) => d.textContent)).toEqual(["0", "1"]);
    await userEvent.click(screen.getByRole("button", { name: "対応待ち" }));
    expect(within(screen.getByRole("table")).getAllByRole("link").map((a) => a.textContent)).toEqual(["#12 #15"]);
    await userEvent.click(screen.getByRole("button", { name: "終わった" }));
    expect(within(screen.getByRole("table")).getAllByRole("link").map((a) => a.textContent)).toEqual(["#8", "#3 #4 #5"]);
    await userEvent.click(screen.getByRole("button", { name: "動いている" }));
    expect(screen.getByText("絞り込みに合う実行はありません")).toBeTruthy();
    expect(table).toBeTruthy();
  });
});

const detail = `${sprints}/sr-0003`;

describe("スプリントの詳細（受け入れ条件 5〜9）", () => {
  it("承認の欄は設計承認ゲート・リリース承認ゲート・判断の 3 つで、設計承認ゲートが承認待ち。仕上げに振り返りと棚卸しが「まだ」で並ぶ", async () => {
    show(detail);
    const gates = await screen.findByRole("region", { name: "承認ゲート" });
    expect(within(gates).getAllByRole("term").map((x) => x.textContent)).toEqual(["設計承認ゲート", "リリース承認ゲート", "判断"]);
    expect(within(gates).getAllByRole("definition").map((x) => x.textContent)).toEqual(["承認待ち", "まだ", "無し"]);
    const required = screen.getByLabelText("必須の工程");
    expect(within(required).getAllByRole("definition").map((x) => x.textContent)).toEqual(["まだ", "まだ"]);
  });

  it("仕上げの記録が届いた実行は、振り返りと棚卸しの終了コードと秒を出す", async () => {
    show(`${sprints}/sr-0001`);
    const required = await screen.findByLabelText("必須の工程");
    expect(within(required).getAllByRole("definition").map((x) => x.textContent)).toEqual(["終了コード 0・300 秒", "終了コード 0・240 秒"]);
  });

  it("課題の欄で工程の記録を絞り、新しい工程が上に並ぶ", async () => {
    show(detail);
    const log = await screen.findByRole("region", { name: "工程の記録" });
    const names = () => within(log).getAllByRole("button", { pressed: false }).filter((b) => b.className.includes("sprint-steps__row")).map((b) => b.querySelector("strong")!.textContent);
    expect(names()).toEqual(["impl（実装）", "sprint-branch（実装）", "design（設計）", "design（設計）"]);
    await userEvent.click(within(log).getByRole("button", { name: "#12" }));
    expect(names()).toEqual(["impl（実装）", "design（設計）"]);
    await userEvent.click(within(log).getByRole("button", { name: "スプリント全体" }));
    expect(names()).toEqual(["sprint-branch（実装）"]);
  });

  it("工程を押すと右が詳細パネルになり、触ったファイル・決めたこと・作業の報告を出し、「概要に戻る」で承認の欄へ戻る", async () => {
    show(detail);
    const log = await screen.findByRole("region", { name: "工程の記録" });
    await userEvent.click(within(log).getByRole("button", { name: /^impl（実装）/ }));
    const panel = await screen.findByRole("region", { name: "工程の詳細" });
    expect(screen.queryByRole("region", { name: "承認ゲート" })).toBeNull();
    expect(await within(panel).findByRole("link", { name: "src/service/handler.ts" })).toBeTruthy();
    expect(within(panel).getByText("+42 −7")).toBeTruthy();
    expect(within(panel).getByText(/見本の問い/)).toBeTruthy();
    expect(within(panel).getByRole("link", { name: "差分を GitHub で見る" }).getAttribute("href")).toContain("/compare/");
    await userEvent.click(within(panel).getByRole("button", { name: "作業の報告を開く" }));
    expect(within(panel).getByText(/作業の報告（見本）/)).toBeTruthy();
    await userEvent.click(within(panel).getByRole("button", { name: "概要に戻る" }));
    expect(screen.getByRole("region", { name: "承認ゲート" })).toBeTruthy();
  });

  it("工程の中身を持たない工程は、各欄に「記録なし」を出す", async () => {
    show(detail);
    const log = await screen.findByRole("region", { name: "工程の記録" });
    await userEvent.click(within(log).getByRole("button", { name: /^sprint-branch/ }));
    const panel = await screen.findByRole("region", { name: "工程の詳細" });
    expect(await within(panel).findAllByText("記録なし")).toHaveLength(3);
  });
});

// 現状固定テスト（I-007: StepPanel の分割の前に、詳細パネルの分岐を固定する）
describe("工程の詳細パネルの分岐（現状固定）", () => {
  function withStep(patch: Partial<StepDetail>): Api {
    const api = createDummyApi();
    const original = api.stepDetails.bind(api);
    api.stepDetails = async (id, sprintRunId, keys) => (await original(id, sprintRunId, keys)).map((s) => ({ ...s, ...patch }));
    return api;
  }

  async function openImpl(api: Api) {
    show(detail, api);
    const log = await screen.findByRole("region", { name: "工程の記録" });
    await userEvent.click(within(log).getByRole("button", { name: /^impl（実装）/ }));
    return screen.findByRole("region", { name: "工程の詳細" });
  }

  it("読み込みに失敗すると、戻るボタンと失敗の知らせだけを出す", async () => {
    const api = createDummyApi();
    api.stepDetails = async () => {
      throw new ApiFailure(503, "unavailable", "x");
    };
    const panel = await openImpl(api);
    expect(await within(panel).findByRole("alert")).toBeTruthy();
    expect(within(panel).getByRole("button", { name: "概要に戻る" })).toBeTruthy();
    expect(within(panel).queryByText("触ったファイル")).toBeNull();
  });

  it("見出しの欄にステップ・終了コード・秒・費用・要約を並べ、費用が無いときは「—」", async () => {
    const panel = await openImpl(withStep({ cost_usd: null, summary: "要約の見本", exit_code: 3, seconds: 1234 }));
    await within(panel).findByText("要約の見本");
    expect(within(panel).getAllByRole("term").map((x) => x.textContent)).toEqual(["ステップ", "終了コード", "秒", "費用", "要約"]);
    expect(within(panel).getAllByRole("definition").map((x) => x.textContent)).toEqual(["impl（実装）", "3", "1,234", "—", "要約の見本"]);
  });

  it("ファイルと決めたことが空のときは「ありません」を出し、差分のリンクが無ければ出さない", async () => {
    const panel = await openImpl(withStep({ files: [], decisions: [], diff_url: null, output: null }));
    expect(await within(panel).findByText("触ったファイルはありません")).toBeTruthy();
    expect(within(panel).getByText("決めたことはありません")).toBeTruthy();
    expect(within(panel).queryByRole("link")).toBeNull();
    expect(within(panel).getAllByText("記録なし")).toHaveLength(1);
  });

  it("URL の無いファイルは文字だけ、行数の無いファイルは「バイナリ」、省いた件数は「ほか N 件」", async () => {
    const panel = await openImpl(
      withStep({
        files: [
          { path: "a.bin", added: null, deleted: 1, url: null },
          { path: "b.ts", added: 1, deleted: 2, url: "https://example.test/b.ts" },
        ],
        files_omitted: 5,
        decisions: [{ question: "Q", answer: "A", by: "user" }],
      }),
    );
    expect(await within(panel).findByText("a.bin")).toBeTruthy();
    expect(within(panel).queryByRole("link", { name: "a.bin" })).toBeNull();
    expect(within(panel).getByText("バイナリ")).toBeTruthy();
    expect(within(panel).getByRole("link", { name: "b.ts" }).getAttribute("href")).toBe("https://example.test/b.ts");
    expect(within(panel).getByText("+1 −2")).toBeTruthy();
    expect(within(panel).getByText("ほか 5 件")).toBeTruthy();
    expect(within(panel).getByText(/Q → A/)).toBeTruthy();
    expect(within(panel).getByText("利用者")).toBeTruthy();
  });

  it("ログの出力は「ログを開く」で開き、切った知らせを出し、「閉じる」で畳む", async () => {
    const panel = await openImpl(withStep({ output: { kind: "log", text: "ログの本文", truncated: true } }));
    const toggle = await within(panel).findByRole("button", { name: "ログを開く" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(within(panel).queryByText("ログの本文")).toBeNull();
    await userEvent.click(toggle);
    expect(within(panel).getByText("ログの本文")).toBeTruthy();
    expect(within(panel).getByText("長いため途中で切りました")).toBeTruthy();
    const close = within(panel).getByRole("button", { name: "閉じる" });
    expect(close.getAttribute("aria-expanded")).toBe("true");
    await userEvent.click(close);
    expect(within(panel).queryByText("ログの本文")).toBeNull();
    expect(within(panel).getByRole("button", { name: "ログを開く" })).toBeTruthy();
  });
});

describe("読み直し（受け入れ条件 10）", () => {
  it("開いたまま新しい事象が来ると、読み直しの間隔の 2 倍以内に出る。読み直しが失敗しても前の表示は消えない", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = createDummyApi();
    let rows: SprintRunRow[] = [sprintRuns[0]!];
    let fail = false;
    api.sprintRuns = async () => {
      if (fail) throw new ApiFailure(503, "unavailable", "x");
      return { sprint_runs: rows };
    };
    show(sprints, api, createDummyAuth(), 1000);
    expect(await screen.findByText("今の工程: impl")).toBeTruthy();
    rows = [{ ...sprintRuns[0]!, current_step: "base-sync" }];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(screen.getByText("今の工程: base-sync")).toBeTruthy();
    fail = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByText("今の工程: base-sync")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toMatch(/^読み直せませんでした（/);
  });

  it("詳細は動いている実行だけを間隔で読み直し、終わった実行は開いたときと「もう一度読む」だけで読む", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = createDummyApi();
    const details = api.sprintRunDetails.bind(api);
    let calls = 0;
    let state = "running";
    api.sprintRunDetails = async (...args: Parameters<Api["sprintRunDetails"]>) => {
      calls += 1;
      return (await details(...args)).map((d) => ({ ...d, state }));
    };
    show(detail, api, createDummyAuth(), 1000);
    await screen.findAllByText(/sr-0003/);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(calls).toBe(2);
    state = "done";
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(calls).toBe(3);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(calls).toBe(3);
  });

  it("バックログは間隔では読み直さず、開いたときと「もう一度読む」だけで読む", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = createDummyApi();
    const backlogs = api.backlogs.bind(api);
    let calls = 0;
    api.backlogs = async (...args: Parameters<Api["backlogs"]>) => {
      calls += 1;
      return backlogs(...args);
    };
    show(`/accounts/${PERSONAL_ID}/repositories/101/backlog`, api, createDummyAuth(), 1000);
    expect(await screen.findByRole("table", { name: "着手の順位" })).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(calls).toBe(1);
    await act(async () => {
      screen.getByRole("button", { name: "もう一度読む" }).click();
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(calls).toBe(2);
  });
});

describe("401 の扱い（受け入れ条件 12）", () => {
  it.each([sprints, detail, `/accounts/${PERSONAL_ID}/repositories/101/backlog`])("%s で API が 401 を返すと token を消してログインへ移る", async (path) => {
    let token: string | null = "t";
    const auth: Auth = { ...createDummyAuth(), getToken: () => token, clearToken: () => (token = null) };
    const api = createApi("https://api.example.test", auth, async () => Response.json({ error: { code: "unauthorized", message: "x" } }, { status: 401 }));
    show(path, api, auth);
    await vi.waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/login"));
    expect(token).toBeNull();
  });
});

describe("バックログ（受け入れ条件 15・16）", () => {
  it("順位の表と棚卸しの記録を出し、書き込む操作を持たない", async () => {
    show(`/accounts/${PERSONAL_ID}/repositories/101/backlog`);
    const ranking = await screen.findByRole("table", { name: "着手の順位" });
    expect(within(ranking).getByText("#21 見本の課題 1")).toBeTruthy();
    expect(within(screen.getByRole("table", { name: "棚卸しの記録" })).getByText("順位を見直した（見本）")).toBeTruthy();
    expect(screen.queryAllByRole("button").filter((b) => !b.closest("header,nav"))).toEqual([]);
  });

  it("順位の表が無いリポジトリは「順位の表がありません」", async () => {
    show(`/accounts/${PERSONAL_ID}/repositories/102/backlog`);
    expect(await screen.findByText("順位の表がありません")).toBeTruthy();
    expect(screen.getByText("棚卸しの記録はまだありません")).toBeTruthy();
  });
});

describe("詳細の注意の欄（#2430 の受け入れ条件 1〜4）", () => {
  const withAttention = (attention: { key: string; at: string; reason: string; summary: string }[]) => {
    const api = createDummyApi();
    const details = api.sprintRunDetails.bind(api);
    api.sprintRunDetails = async (...args: Parameters<Api["sprintRunDetails"]>) => (await details(...args)).map((d) => ({ ...d, attention }));
    return api;
  };

  it("base_conflict は「取り込みで解けない衝突」、ほかの理由は値そのものを、要約と利用者の時刻帯の日時とともに記録された順に出す", async () => {
    show(
      detail,
      withAttention([
        { key: "a1", at: "2026-11-02T00:20:00Z", reason: "base_conflict", summary: "src/a.ts で衝突" },
        { key: "a2", at: "2026-11-02T00:10:00Z", reason: "stopped", summary: "止まった" },
        { key: "a3", at: "2026-11-02T00:30:00Z", reason: "toString", summary: "名前の衝突しない値" },
      ]),
    );
    const region = await screen.findByRole("region", { name: "注意" });
    const items = within(region).getAllByRole("listitem");
    expect(items.map((li) => li.querySelector("strong")!.textContent)).toEqual(["取り込みで解けない衝突", "stopped", "toString"]);
    expect(items[0]!.textContent).toContain("src/a.ts で衝突");
    expect(items[0]!.textContent).toContain("2026/11/2 9:20:00");
    expect(items[1]!.textContent).toContain("止まった");
  });

  it("注意が 0 件なら欄に「ありません」を出す", async () => {
    show(detail, withAttention([]));
    const region = await screen.findByRole("region", { name: "注意" });
    expect(within(region).getByText("ありません")).toBeTruthy();
    expect(within(region).queryAllByRole("listitem")).toHaveLength(0);
  });

  it("欄は詳細の読み出しだけで描き、ほかの読み出しを打たない", async () => {
    const api = createDummyApi();
    const approvals = vi.fn(api.approvalRequests);
    api.approvalRequests = approvals;
    show(`${sprints}/sr-0002`, api);
    const region = await screen.findByRole("region", { name: "注意" });
    expect(within(region).getByText("取り込みで解けない衝突")).toBeTruthy();
    expect(approvals).not.toHaveBeenCalled();
  });
});
