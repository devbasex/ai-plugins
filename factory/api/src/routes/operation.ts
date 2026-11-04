// 操作の登録の層（#2384 の決定 5・不変条件 I10・I12・I13）。経路を操作の表から登録し、要素の数と重複の検査・
// 経路のアカウントの解決・要素ごとの実行・操作の記録へ渡す値を、すべての利用者向けの操作に同じく掛ける
import type { Context, Hono } from "hono";
import { MAX_ITEMS, operation, type Operation, type OperationId } from "@ndf-factory/contracts/operations";
import type { AccountContext } from "../accounts/model.ts";
import { resolveFromRequest, type AuthEnv, type OperationTrace } from "../auth/resolve.ts";
import { InvalidRequestError } from "../errors.ts";
import { runElements, type ElementResult } from "./elements.ts";

export type Element = Record<string, unknown>;

export interface ElementHandler<S> {
  // 要求全体の前に 1 度だけ行う（条件の照合など）。投げると要求全体を断る
  prepare?: (c: Context<AuthEnv>, ctx: AccountContext | null) => Promise<S>;
  // 要素 1 つの値を返す。ApiError を投げるとその要素だけが失敗の結果になる
  run: (element: Element, ctx: AccountContext | null, shared: S, c: Context<AuthEnv>) => Promise<unknown>;
}

const honoPath = (op: Operation) => op.path.replace("{accountId}", ":accountId");
const method = (op: Operation) => op.method.toLowerCase() as "get" | "post" | "put" | "patch" | "delete";

// 経路にアカウントがあれば今と同じく解決し、権限が無ければ要求全体を 403 にする（決定 2）
async function contextOf(c: Context<AuthEnv>, op: Operation): Promise<AccountContext | null> {
  if (!op.path.includes("{accountId}")) return null;
  return resolveFromRequest(c, c.req.param("accountId"));
}

function trace(c: Context<AuthEnv>, op: Operation): OperationTrace {
  const t: OperationTrace = { operationId: op.operationId, accountId: op.path.includes("{accountId}") ? (c.req.param("accountId") ?? null) : null, targetIds: [], reasons: [] };
  c.set("operation", t);
  return t;
}

function queryValue(op: Operation, key: string, raw: string): unknown {
  const schema = op.parameters.find((p) => p.name === key)?.schema as { items?: { type?: string } } | undefined;
  return schema?.items?.type === "integer" && /^\d+$/.test(raw) ? Number(raw) : raw;
}

// 要素を取り出す。鍵の query の繰り返しか、本文の配列。鍵を持たない操作の取得と削除は要素が必ず 1 個
async function elementsOf(c: Context<AuthEnv>, op: Operation): Promise<unknown[]> {
  if (op.kind === "select" || op.kind === "delete") {
    if (op.keys.length === 0) return [{}];
    const key = op.keys[0]!;
    return (c.req.queries(key) ?? []).map((raw) => ({ [key]: queryValue(op, key, raw) }));
  }
  let body: unknown;
  try {
    body = await c.req.json();
  } catch {
    throw new InvalidRequestError("本文は要素の配列にしてください");
  }
  if (!Array.isArray(body)) throw new InvalidRequestError("本文は要素の配列にしてください");
  return body;
}

const isElement = (v: unknown): v is Element => typeof v === "object" && v !== null && !Array.isArray(v);

// 要素の数と重複の検査（I12）。どれかに当たれば、どの要素も処理しない
function checkElements(op: Operation, elements: unknown[]): void {
  if (elements.length === 0) throw new InvalidRequestError("要素を 1 個以上送ってください", "empty_items");
  if (elements.length > MAX_ITEMS) {
    throw new InvalidRequestError(`1 回に送れる要素は ${MAX_ITEMS} 個までです`, "too_many_items", { limit: MAX_ITEMS });
  }
  const seen = new Set<string>();
  for (const e of elements) {
    const id = op.keys.length === 0 ? "" : JSON.stringify(op.keys.map((k) => (isElement(e) ? (e[k] ?? null) : null)));
    if (seen.has(id)) {
      throw new InvalidRequestError("同じ鍵の要素が 2 つあります", "duplicate_key", { key: op.keys.length === 0 ? null : op.keys.join("+") });
    }
    seen.add(id);
  }
}

function pick(value: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((v, k) => (isElement(v) ? v[k] : undefined), value);
}

// 操作の記録へ渡す対象の ID（要素の鍵と作った識別子）と、失敗した要素の理由
function record(t: OperationTrace, op: Operation, elements: unknown[], results: ElementResult[]): void {
  elements.forEach((e, i) => {
    for (const k of op.keys) {
      const v = isElement(e) ? e[k] : undefined;
      if (typeof v === "string" || typeof v === "number") t.targetIds.push(`${k}=${v}`);
    }
    const r = results[i];
    if (op.idField && r?.ok) {
      const v = pick(r.value, op.idField);
      if (typeof v === "string" || typeof v === "number") t.targetIds.push(`${op.idField.split(".").at(-1)}=${v}`);
    }
    if (r && !r.ok && !t.reasons.includes(r.error.code)) t.reasons.push(r.error.code);
  });
}

// 一覧（条件で集めた値の配列。要素の結果で包まない）
export function registerList(
  app: Hono<AuthEnv>,
  operationId: OperationId,
  list: (c: Context<AuthEnv>, ctx: AccountContext | null) => Promise<unknown[]>,
): void {
  const op = operation(operationId);
  if (op.kind !== "list") throw new Error(`${operationId} は一覧ではありません`);
  app[method(op)](honoPath(op), async (c) => {
    trace(c, op);
    const ctx = await contextOf(c, op);
    return c.json(await list(c, ctx));
  });
}

// 一覧を除く操作（指定の取得・作成・更新・削除・操作）
export function registerOperation<S = undefined>(app: Hono<AuthEnv>, operationId: OperationId, handler: ElementHandler<S>): void {
  const op = operation(operationId);
  if (op.kind === "list") throw new Error(`${operationId} は一覧です`);
  app[method(op)](honoPath(op), async (c) => {
    const t = trace(c, op);
    const elements = await elementsOf(c, op);
    checkElements(op, elements);
    const ctx = await contextOf(c, op);
    const shared = (handler.prepare ? await handler.prepare(c, ctx) : undefined) as S;
    const results = await runElements(elements, (e) => {
      if (!isElement(e)) throw new InvalidRequestError("要素はオブジェクトにしてください");
      return handler.run(e, ctx, shared, c);
    });
    record(t, op, elements, results);
    return c.json(results);
  });
}
