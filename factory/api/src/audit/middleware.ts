// 操作の記録の書き手（#2384 の F6）。利用者向けの要求 1 回ごとに、結果（成功・一部の失敗・400・401・403・404）によらず
// 記録を 1 件追記する。追記に失敗したら記録の全体を構造化ログへ出し、応答を 503 audit_unavailable にする（決定 11・I7）
import { randomUUID } from "node:crypto";
import type { Context, MiddlewareHandler } from "hono";
import { OPERATIONS, findOperation, type Operation } from "@ndf-factory/contracts/operations";
import { isDelegationAuthorization, isMcpInternal, type AuthEnv } from "../auth/resolve.ts";
import { ServiceUnavailableError } from "../errors.ts";
import { toItem, type AuditLog, type AuditOutcome, type AuditRecord } from "./log.ts";

// MCP の受け口そのものへの要求は操作ではないため記録しない（内部の要求をそれぞれ記録する）
export const MCP_PATH = "/mcp";

const PATTERNS = OPERATIONS.map((op) => ({
  op: op as Operation,
  re: new RegExp(`^${op.path.replace("{accountId}", "([^/]+)")}$`),
}));

// 操作の登録の層を通らなかった要求（401 など）の操作を、メソッドと経路から引く
function match(method: string, path: string): { op: Operation; accountId: string | null } | null {
  for (const { op, re } of PATTERNS) {
    if (op.method !== method) continue;
    const m = re.exec(path);
    if (m) return { op, accountId: m[1] ? decodeURIComponent(m[1]) : null };
  }
  return null;
}

// API Gateway の要求の ID（Lambda の環境）。無ければ UUID
export function requestIdOf(c: Context<AuthEnv>): string {
  const env = c.env as { event?: { requestContext?: { requestId?: unknown } } } | undefined;
  const id = env?.event?.requestContext?.requestId;
  return typeof id === "string" && id !== "" ? id : randomUUID();
}

async function recordOf(c: Context<AuthEnv>, at: string): Promise<AuditRecord> {
  const caller = c.get("caller");
  const trace = c.get("operation");
  // 呼び出し元の種類は資格情報の種類と内部の印だけで決め、外から届くほかのヘッダーを見ない（I16）
  const delegated = isDelegationAuthorization(c.req.header("Authorization"));
  const via = caller?.via ?? (isMcpInternal(c.env) ? "mcp" : delegated ? "api" : "screen");
  const matched = trace ? { op: findOperation(trace.operationId), accountId: trace.accountId } : match(c.req.method, c.req.path);
  const status = c.res.status;
  let outcome: AuditOutcome = "ok";
  let reason: string | null = null;
  if (status !== 200) {
    outcome = "rejected";
    const body = (await c.res
      .clone()
      .json()
      .catch(() => null)) as { error?: { code?: unknown } } | null;
    reason = typeof body?.error?.code === "string" ? body.error.code : null;
  } else if (trace && trace.reasons.length > 0) {
    outcome = "partial";
    reason = trace.reasons.join(",");
  }
  return {
    recordId: c.get("requestId") ?? requestIdOf(c),
    at,
    userId: caller?.userId ?? null,
    credentialId: caller?.delegation?.credentialId ?? null,
    via,
    operationId: matched?.op?.operationId ?? null,
    accountId: matched?.accountId ?? null,
    targetIds: trace?.targetIds ?? [],
    outcome,
    status,
    reason,
  };
}

export function recordOperations(log: () => AuditLog | null | undefined, fallback: (line: Record<string, unknown>) => void): MiddlewareHandler<AuthEnv> {
  return async (c, next) => {
    const at = new Date().toISOString();
    await next();
    if (c.req.method === "OPTIONS" || c.req.path === MCP_PATH) return;
    const writer = log();
    if (!writer) return;
    const record = await recordOf(c, at);
    try {
      await writer.append(record);
    } catch {
      fallback({ audit_fallback: true, record: toItem(record) });
      const err = new ServiceUnavailableError("audit_unavailable");
      c.res = new Response(JSON.stringify(err.body()), { status: 503, headers: { "Content-Type": "application/json" } });
    }
  };
}
