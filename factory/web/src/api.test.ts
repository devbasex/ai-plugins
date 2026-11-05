// 画面の API クライアントと契約の対応（#2384 の受け入れ条件 1、性能の条件）
import { describe, expect, it } from "vitest";
import { MAX_ITEMS, OPERATIONS } from "@ndf-factory/contracts/operations";
import { ApiFailure, createApi, type Api } from "./api.ts";
import type { Auth } from "./auth.ts";

const API_URL = "https://api.example.test";
const auth = { getToken: () => "t", clearToken: () => {} } as unknown as Auth;

interface Sent {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
}

// 送った要求を記録し、要素の数だけ成功の結果を返す偽の API
function recorder() {
  const sent: Sent[] = [];
  const doFetch: typeof fetch = async (input, init) => {
    const url = new URL(String(input));
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    sent.push({ method: init?.method ?? "GET", path: url.pathname, query: url.searchParams, body });
    const op = OPERATIONS.find((o) => o.method === (init?.method ?? "GET") && new RegExp(`^${o.path.replace("{accountId}", "[^/]+")}$`).test(url.pathname));
    if (!op || op.kind === "list") return Response.json([{}]);
    const n = Array.isArray(body) ? body.length : op.keys.length === 0 ? 1 : url.searchParams.getAll(op.keys[0]!).length;
    return Response.json(Array.from({ length: n }, () => ({ ok: true, value: {} })));
  };
  return { sent, api: createApi(API_URL, auth, doFetch) };
}

// Api のメソッドをすべて 1 回ずつ呼ぶ。メソッドを足すとここが型で落ちる（呼び忘れを防ぐ）
const CALLS: { [K in keyof Api]: (api: Api) => Promise<unknown> } = {
  me: (a) => a.me(),
  account: (a) => a.account("p-1"),
  createTeam: (a) => a.createTeam("t", "standard"),
  setExecutionMode: (a) => a.setExecutionMode("p-1", "byok"),
  addSeat: (a) => a.addSeat("t-1", "u", "standard"),
  changeSeat: (a) => a.changeSeat("t-1", "u", "premium"),
  removeSeat: (a) => a.removeSeat("t-1", "u"),
  installUrl: (a) => a.installUrl("p-1"),
  authorizeUrl: (a) => a.authorizeUrl("p-1"),
  repositoryCandidates: (a) => a.repositoryCandidates("p-1", "c", "s"),
  repositories: (a) => a.repositories("p-1"),
  registerRepository: (a) => a.registerRepository("p-1", "proof", 1),
  removeRepository: (a) => a.removeRepository("p-1", 1),
  modelCredential: (a) => a.modelCredential("p-1"),
  saveModelCredential: (a) => a.saveModelCredential("p-1", { kind: "anthropic_api_key", api_key: "k" }),
  deleteModelCredential: (a) => a.deleteModelCredential("p-1"),
  issueCandidates: (a) => a.issueCandidates("p-1", 1, []),
  startSprintRun: (a) => a.startSprintRun("p-1", 1, [3]),
  sprintRuns: (a) => a.sprintRuns("p-1"),
  sprintRunDetails: (a) => a.sprintRunDetails("p-1", ["s1"]),
  stepDetails: (a) => a.stepDetails("p-1", "s1", ["k1"]),
  backlogs: (a) => a.backlogs("p-1", [1]),
  cancelSprintRun: (a) => a.cancelSprintRun("p-1", "s1"),
  approvalRequests: (a) => a.approvalRequests("p-1"),
  updateRepository: (a) => a.updateRepository("p-1", 1, null),
  loops: (a) => a.loops("p-1", 1),
  startLoop: (a) => a.startLoop("p-1", 1, { name: "l", milestones: ["m"] }),
  loopDetails: (a) => a.loopDetails("p-1", ["l1"]),
  updateLoopBudget: (a) => a.updateLoopBudget("p-1", "l1", {}),
  cancelLoop: (a) => a.cancelLoop("p-1", "l1"),
  requestNextLap: (a) => a.requestNextLap("p-1", "l1"),
  answerLoop: (a) => a.answerLoop("p-1", "l1", 3, "approved"),
  delegations: (a) => a.delegations(),
  createDelegation: (a) => a.createDelegation({ client_name: "c", scope: "read", expires_in_seconds: 900 }),
  revokeDelegation: (a) => a.revokeDelegation("d1"),
};

describe("画面の API クライアントと契約（受け入れ条件 1）", () => {
  it("どのメソッドの要求も契約の利用者向けの操作に当たり、本文は配列である", async () => {
    const { sent, api } = recorder();
    for (const [name, call] of Object.entries(CALLS)) {
      const before = sent.length;
      await call(api);
      expect(sent.length, name).toBeGreaterThan(before);
    }
    for (const s of sent) {
      const op = OPERATIONS.find((o) => o.method === s.method && new RegExp(`^${o.path.replace("{accountId}", "[^/]+")}$`).test(s.path));
      expect(op, `${s.method} ${s.path}`).toBeDefined();
      if (s.body !== undefined) expect(Array.isArray(s.body), `${s.method} ${s.path}`).toBe(true);
    }
  });

  it("新しい識別子を作る作成には冪等の鍵を付け、呼ぶたびに変える", async () => {
    const { sent, api } = recorder();
    await api.startSprintRun("p-1", 1, [3]);
    await api.startSprintRun("p-1", 1, [3]);
    const keys = sent.map((s) => (s.body as { idempotency_key: string }[])[0]!.idempotency_key);
    expect(keys[0]).toMatch(/^[0-9A-Za-z-]{1,64}$/);
    expect(keys[0]).not.toBe(keys[1]);
  });

  it("結果が分からない失敗（503・通信の失敗）の後の同じ中身の送り直しは同じ鍵を使い、断りの後は変える", async () => {
    const keys: string[] = [];
    const replies = [503, 0, 200, 409, 200];
    const doFetch: typeof fetch = async (_input, init) => {
      keys.push((JSON.parse(String(init?.body)) as { idempotency_key: string }[])[0]!.idempotency_key);
      const status = replies.shift()!;
      if (status === 0) throw new TypeError("Failed to fetch");
      if (status === 503) return Response.json({ error: { code: "audit_unavailable", message: "" } }, { status });
      if (status === 409) return Response.json([{ ok: false, status, error: { code: "limits_exceeded", message: "" } }]);
      return Response.json([{ ok: true, value: {} }]);
    };
    const api = createApi(API_URL, auth, doFetch);
    await expect(api.createTeam("t", "standard")).rejects.toBeInstanceOf(ApiFailure);
    await expect(api.createTeam("t", "standard")).rejects.toBeInstanceOf(TypeError);
    await api.createTeam("t", "standard");
    await expect(api.createTeam("t", "standard")).rejects.toBeInstanceOf(ApiFailure);
    await api.createTeam("t", "standard");
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).toBe(keys[0]);
    expect(keys[3]).not.toBe(keys[0]);
    expect(keys[4]).not.toBe(keys[3]);
  });
});

describe("要求の回数（非機能の条件「性能・拡張性」）", () => {
  it("詳細 3 件は 1 回の要求で読み、上限を超える数は 100 ずつに分ける", async () => {
    const { sent, api } = recorder();
    expect(await api.sprintRunDetails("p-1", ["a", "b", "c"])).toHaveLength(3);
    expect(sent).toHaveLength(1);
    expect(sent[0]!.query.getAll("sprint_run_id")).toEqual(["a", "b", "c"]);
    sent.length = 0;
    await api.loopDetails("p-1", Array.from({ length: MAX_ITEMS + 1 }, (_, i) => `l${i}`));
    expect(sent.map((s) => s.query.getAll("loop_id").length)).toEqual([MAX_ITEMS, 1]);
  });

  it("0 件の取得は要求を送らない", async () => {
    const { sent, api } = recorder();
    expect(await api.backlogs("p-1", [])).toEqual([]);
    expect(sent).toHaveLength(0);
  });
});
