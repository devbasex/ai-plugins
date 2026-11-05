// MCP の受け口（#2384 の受け入れ条件 2・3・17・18、不変条件 I11・I15・I16）
import { Hono } from "hono";
import { beforeEach, describe, expect, it } from "vitest";
import { OPERATIONS } from "@ndf-factory/contracts/operations";
import type { AuthEnv } from "../auth/resolve.ts";
import { createTestApi, firstResult, type TestApi } from "../testing/app.ts";
import { registerMcp, tools, type AppRequest } from "./server.ts";

let api: TestApi;
let credential: string;
let account: string;
let id = 0;

beforeEach(async () => {
  api = await createTestApi({ factory: true });
  account = (await (await api.op("getMe", { user: "u-1" })).json())[0].personal_account_id;
  const issued = await firstResult(
    await api.op("createDelegation", { user: "u-1", items: [{ client_name: "c", scope: "read_write", expires_in_seconds: 3600, idempotency_key: "m" }] }),
  );
  credential = issued.body.credential;
});

async function rpc(method: string, params: unknown, authorization = `Bearer ${credential}`, headers: Record<string, string> = {}) {
  const res = await api.app.request("/mcp", {
    method: "POST",
    headers: { Authorization: authorization, "Content-Type": "application/json", Accept: "application/json, text/event-stream", ...headers },
    body: JSON.stringify({ jsonrpc: "2.0", id: ++id, method, params }),
  });
  return { status: res.status, body: (await res.json().catch(() => null)) as any };
}

const callTool = async (name: string, args: Record<string, unknown>) => (await rpc("tools/call", { name, arguments: args })).body.result;

describe("ツールと契約の対応（受け入れ条件 2、I11）", () => {
  it("tools/list のツールの名前の集合が、契約の利用者向けの操作と一致する", async () => {
    const { status, body } = await rpc("tools/list", {});
    expect(status).toBe(200);
    expect(body.result.tools.map((t: { name: string }) => t.name).sort()).toEqual(OPERATIONS.map((o) => o.operationId).sort());
  });

  it("人だけが確定できるツールは説明の頭で断られることを示し、入力は操作の形を持つ", () => {
    const byName = new Map(tools().map((t) => [t.name, t]));
    expect(byName.get("answerLoopPending")!.description).toMatch(/^人だけが確定できる/);
    expect(byName.get("startSprintRun")!.inputSchema.required).toEqual(["account_id", "items"]);
    expect(byName.get("getSprintRunDetail")!.inputSchema.properties.sprint_run_id).toMatchObject({ type: "array", maxItems: 100 });
    expect(byName.get("getMe")!.inputSchema.required).toEqual([]);
  });
});

describe("ツールの呼び出し（受け入れ条件 3・17・18、I15・I16）", () => {
  it("API の経路と同じ結果を返し、操作の記録に mcp として残る。tools/list は記録しない", async () => {
    const direct = await (await api.op("listSprintRuns", { authorization: `Bearer ${credential}`, accountId: account })).json();
    const before = api.audit.items.length;
    await rpc("tools/list", {});
    expect(api.audit.items.length).toBe(before);
    const r = await callTool("listSprintRuns", { account_id: account });
    expect(r.structuredContent).toEqual({ status: 200, body: direct });
    expect(r.isError).toBe(false);
    expect(JSON.parse(r.content[0].text)).toEqual(r.structuredContent);
    expect(api.audit.items.at(-1)).toMatchObject({ operation_id: "listSprintRuns", via: "mcp", user_id: "u-1", outcome: "ok" });
    expect(api.audit.items.length).toBe(before + 1);
  });

  it("人だけが確定できるツールは 403 human_only を返し、答えも状態も保存しない", async () => {
    const r = await callTool("answerLoopPending", { account_id: account, items: [{ loop_id: "l", issue: 1, decision: "approved" }] });
    expect(r.isError).toBe(true);
    expect(r.structuredContent).toMatchObject({ status: 403, body: { error: { code: "human_only" } } });
    expect(api.h!.table.loops.size).toBe(0);
  });

  it("承認の依頼の材料（スプリントの詳細）は読める。要素の結果はそのまま返る", async () => {
    const r = await callTool("getSprintRunDetail", { account_id: account, sprint_run_id: ["sr-none"] });
    expect(r.structuredContent.status).toBe(200);
    expect(r.structuredContent.body[0]).toMatchObject({ ok: false, error: { code: "forbidden" } });
  });

  it("アカウントが要るツールに account_id が無ければ 400 で、アプリへ送らない", async () => {
    const r = await callTool("listSprintRuns", {});
    expect(r.structuredContent.status).toBe(400);
  });

  it("受け口が受け取る依存はアプリへの要求の関数だけで、ツールの呼び出しはすべてそこを通る", async () => {
    const seen: string[] = [];
    const app = new Hono<AuthEnv>();
    app.use("*", async (c, next) => {
      c.set("caller", { userId: "u", idp: "google", delegation: { credentialId: "x", scope: "read" }, via: "api" });
      await next();
    });
    const call: AppRequest = async (path, init) => {
      seen.push(`${init.method} ${path}`);
      return Response.json([]);
    };
    registerMcp(app, call, () => null);
    const res = await app.request("/mcp", {
      method: "POST",
      headers: { Authorization: "Bearer ndfd_x", "Content-Type": "application/json", Accept: "application/json, text/event-stream" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "listLoops", arguments: { account_id: "a", repository_id: 1 } } }),
    });
    expect(res.status).toBe(200);
    expect(seen).toEqual(["GET /accounts/a/loops?repository_id=1"]);
  });
});

describe("受け口の資格情報（決定 7）", () => {
  it("Cognito の token は 401、資格情報が無ければ 401", async () => {
    expect((await rpc("tools/list", {}, await api.tokens.bearer("u-1"))).status).toBe(401);
    expect((await rpc("tools/list", {}, "")).status).toBe(401);
  });

  it("画面と違うオリジンからの要求は 403、GET と DELETE は 405", async () => {
    expect((await rpc("tools/list", {}, `Bearer ${credential}`, { Origin: "https://evil.example.com" })).status).toBe(403);
    for (const method of ["GET", "DELETE"]) {
      expect((await api.app.request("/mcp", { method, headers: { Authorization: `Bearer ${credential}` } })).status).toBe(405);
    }
  });
});
