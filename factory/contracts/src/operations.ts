// 操作の表（#2384 の決定 5）。契約から生成した利用者向けの操作の一覧を、画面・API・MCP・検査が同じく読む
import { OPERATIONS } from "./generated/operations.ts";

export { OPERATIONS };

// 操作の扱い。read（読み取り）・write（書き込み）・human（人だけが確定できる。委任の資格情報からは断る）
export type OperationAccess = "read" | "write" | "human";
// 操作の種類。list は条件で集めた値の配列を返し、ほかは要素の結果を入力と同じ順に返す
export type OperationKind = "list" | "select" | "create" | "update" | "delete" | "action";
export type OperationMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface OperationParameter {
  readonly name: string;
  readonly required: boolean;
  // 鍵の query は繰り返す（?a=1&a=2）
  readonly array: boolean;
  readonly description: string | null;
  readonly schema: unknown;
}

export interface Operation {
  readonly operationId: string;
  readonly method: OperationMethod;
  // 経路の変数は {accountId} だけ
  readonly path: string;
  readonly kind: OperationKind;
  readonly access: OperationAccess;
  // 要素の鍵の項目の名前。鍵を持たない操作は空で、要素は必ず 1 個
  readonly keys: readonly string[];
  // 作成の成功の値の中の、作った識別子の項目（`.` で入れ子を指す）
  readonly idField: string | null;
  readonly summary: string;
  readonly description: string;
  readonly parameters: readonly OperationParameter[];
  // 作成・更新・操作の本文の要素の定義（参照をほどいた JSON Schema）
  readonly itemSchema: unknown;
  readonly source: string;
}

export type OperationId = (typeof OPERATIONS)[number]["operationId"];

// 1 回の要求で受け渡す要素の上限（要求の前提 3）
export const MAX_ITEMS = 100;

// 操作の記録の target_ids へ写してよい鍵の名前（契約の表の「操作の記録へ写す鍵」。決定 12）
export const AUDIT_KEYS = [
  "account_id",
  "user_id",
  "repository_id",
  "sprint_run_id",
  "step_key",
  "loop_id",
  "issue",
  "seq",
  "credential_id",
  "idempotency_key",
] as const;

const byId = new Map<string, Operation>(OPERATIONS.map((op) => [op.operationId, op as Operation]));

export function operation(operationId: OperationId): Operation {
  return byId.get(operationId)!;
}

export function findOperation(operationId: string): Operation | null {
  return byId.get(operationId) ?? null;
}

// 経路の {accountId} を埋め、query を足した URL の経路を返す。配列の値は同じ名前で繰り返す
export function operationUrl(op: Operation, accountId: string | null, query: Record<string, unknown> = {}): string {
  let path = op.path;
  if (path.includes("{accountId}")) {
    if (accountId === null) throw new Error(`${op.operationId} はアカウントの ID が要ります`);
    path = path.replace("{accountId}", encodeURIComponent(accountId));
  }
  const params = new URLSearchParams();
  for (const [name, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    for (const v of Array.isArray(value) ? value : [value]) params.append(name, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}
