// MCP の受け口（#2384 の F4・決定 6・7・14）。POST /mcp で MCP の要求を受け、操作の表から作ったツールの呼び出しを、
// 同じアプリの経路へ内部の要求として送る。認証・範囲の照合・要素の検査・認可・記録は画面と API と同じ処理を通る（I15）。
// 状態を持たず、要求ごとに JSON の 1 応答で返す。委任の資格情報だけを受ける
import type { Hono } from "hono";
import { Server, WebStandardStreamableHTTPServerTransport, type Tool } from "@modelcontextprotocol/server";
import { MAX_ITEMS, OPERATIONS, findOperation, operationUrl, type Operation } from "@ndf-factory/contracts/operations";
import { MCP_INTERNAL, type AuthEnv } from "../auth/resolve.ts";
import { AuthError, ForbiddenError } from "../errors.ts";
import { MCP_PATH } from "../audit/middleware.ts";

// アプリへ内部の要求を送る関数（Hono の app.request）。MCP の受け口が受け取る依存はこれだけ
export type AppRequest = (path: string, init: RequestInit, env: Record<symbol, unknown>) => Response | Promise<Response>;

export const SERVER_INFO = { name: "ndf-factory", version: "0.1.0" } as const;

const HUMAN_NOTE = "人だけが確定できる。MCP からは常に断られる。画面で行う。";

const BODY_KINDS = new Set(["create", "update", "action"]);

export interface ToolDefinition {
  name: string;
  description: string;
  inputSchema: { type: "object"; properties: Record<string, unknown>; required: string[] };
}

// ツールの定義を操作の表から作る。手で書いたツールの定義を持たない（契約の表の「MCP のツール」）
export function toolOf(op: Operation): ToolDefinition {
  const properties: Record<string, unknown> = {};
  const required: string[] = [];
  if (op.path.includes("{accountId}")) {
    properties.account_id = { type: "string", description: "アカウントの ID" };
    required.push("account_id");
  }
  if (BODY_KINDS.has(op.kind)) {
    properties.items = { type: "array", minItems: 1, maxItems: MAX_ITEMS, items: op.itemSchema ?? { type: "object" }, description: "要素の配列。結果は同じ順に返る" };
    required.push("items");
  } else {
    for (const p of op.parameters) {
      properties[p.name] = { ...(p.schema as object), ...(p.description ? { description: p.description } : {}) };
      if (p.required) required.push(p.name);
    }
  }
  const text = [op.summary, op.description].filter((s) => s !== "").join("\n");
  return { name: op.operationId, description: op.access === "human" ? `${HUMAN_NOTE}\n${text}` : text, inputSchema: { type: "object", properties, required } };
}

export const tools = (): ToolDefinition[] => OPERATIONS.map((op) => toolOf(op as Operation));

function result(status: number, body: unknown) {
  const value = { status, body };
  return { content: [{ type: "text" as const, text: JSON.stringify(value) }], structuredContent: value, isError: status >= 400 };
}

// ツールの入力から経路・query・本文を組み、Authorization をそのまま付けて内部の要求として送る
async function callTool(call: AppRequest, authorization: string, name: string, args: Record<string, unknown>) {
  const op = findOperation(name);
  if (!op) return result(400, { error: { code: "unknown_tool", message: `${name} というツールはありません` } });
  const accountId = typeof args.account_id === "string" ? args.account_id : null;
  if (op.path.includes("{accountId}") && accountId === null) return result(400, { error: { code: "invalid_request", message: "account_id が要ります" } });
  const query: Record<string, unknown> = {};
  if (!BODY_KINDS.has(op.kind)) for (const p of op.parameters) if (args[p.name] !== undefined) query[p.name] = args[p.name];
  const hasBody = BODY_KINDS.has(op.kind);
  const res = await call(
    operationUrl(op, accountId, query),
    {
      method: op.method,
      headers: { Authorization: authorization, ...(hasBody ? { "Content-Type": "application/json" } : {}) },
      body: hasBody ? JSON.stringify(args.items ?? null) : undefined,
    },
    { [MCP_INTERNAL]: true },
  );
  return result(res.status, await res.json().catch(() => null));
}

export function registerMcp(app: Hono<AuthEnv>, call: AppRequest, webOrigin: () => string | null): void {
  app.post(MCP_PATH, async (c) => {
    // 委任の資格情報だけを受ける（Cognito の token は 401）
    if (!c.get("caller").delegation) throw new AuthError();
    // ブラウザからの要求は画面のオリジンだけを受ける（Streamable HTTP の Origin の検査）
    const origin = c.req.header("Origin");
    if (origin !== undefined && origin !== webOrigin()) throw new ForbiddenError();
    const authorization = c.req.header("Authorization")!;
    const server = new Server(SERVER_INFO, { capabilities: { tools: {} } });
    // 定義は契約から作った JSON Schema（JSON の値だけを持つ）
    server.setRequestHandler("tools/list", async () => ({ tools: tools() as unknown as Tool[] }));
    server.setRequestHandler("tools/call", async (req) => callTool(call, authorization, req.params.name, (req.params.arguments ?? {}) as Record<string, unknown>));
    const transport = new WebStandardStreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
    await server.connect(transport);
    return transport.handleRequest(c.req.raw);
  });
  // 状態を持たないため、GET のストリームとセッションの終わりは受けない
  for (const method of ["get", "delete"] as const) app[method](MCP_PATH, (c) => c.body(null, 405));
}
