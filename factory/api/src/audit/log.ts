// 操作の記録（#2384 の集約「操作の記録」・不変条件 I7〜I9）。利用者向けの操作 1 回に 1 件を追記する。
// 保存の契約は追記の関数 1 つだけを持ち、書き換えと削除の関数を持たない（I8）。
// 記録は要求の本文を写さず、許可した鍵の値と固定の列だけで組む（決定 12・I9）
import { AUDIT_KEYS } from "@ndf-factory/contracts/operations";
import type { Via } from "../accounts/model.ts";

export type AuditOutcome = "ok" | "partial" | "rejected";

export interface AuditRecord {
  // 要求の識別子（API Gateway の要求の ID、無ければ UUID）。構造化ログの同じ要求の行とたどれる
  recordId: string;
  // 受けた日時（UTC、ミリ秒まで）
  at: string;
  // 空は 401 で利用者を決められなかった
  userId: string | null;
  // 空は人の直の操作か、401 で決められなかった
  credentialId: string | null;
  via: Via;
  // 空は契約に無い経路
  operationId: string | null;
  accountId: string | null;
  // <鍵の名前>=<値>。許可した鍵だけ
  targetIds: string[];
  outcome: AuditOutcome;
  status: number;
  // rejected の誤りの code。partial は失敗した要素の code を重ねずに , で並べる。ok は空
  reason: string | null;
}

export interface AuditLog {
  append(record: AuditRecord): Promise<void>;
}

// 記録の列の名前（表の列と同じ）。この一覧の外の列を持たない
export const AUDIT_COLUMNS = [
  "record_id",
  "at",
  "user_id",
  "credential_id",
  "via",
  "operation_id",
  "account_id",
  "target_ids",
  "outcome",
  "status",
  "reason",
] as const;

const ALLOWED: ReadonlySet<string> = new Set(AUDIT_KEYS);

// 許可した鍵の値だけを残す（鍵の名前が一覧に無い値・形の違う値は捨てる）
export function allowedTargets(targets: readonly string[]): string[] {
  const out: string[] = [];
  for (const t of targets) {
    const i = t.indexOf("=");
    if (i < 1 || !ALLOWED.has(t.slice(0, i))) continue;
    if (!out.includes(t)) out.push(t);
  }
  return out;
}

export function toItem(r: AuditRecord): Record<(typeof AUDIT_COLUMNS)[number], unknown> {
  return {
    record_id: r.recordId,
    at: r.at,
    user_id: r.userId,
    credential_id: r.credentialId,
    via: r.via,
    operation_id: r.operationId,
    account_id: r.accountId,
    target_ids: allowedTargets(r.targetIds),
    outcome: r.outcome,
    status: r.status,
    reason: r.reason,
  };
}
