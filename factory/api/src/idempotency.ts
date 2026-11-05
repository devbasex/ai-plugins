// 作成の冪等の鍵（#2384 の決定 4・不変条件 I14）。新しい識別子を作る作成で、同じ利用者・同じ操作・同じ鍵の
// 24 時間以内の作成を 1 件にする。鍵の行は作るものと同じ表へ同じトランザクションで書く
import { InvalidRequestError } from "./errors.ts";

export const IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60;
const KEY_PATTERN = /^[0-9A-Za-z-]{1,64}$/;

// 鍵の行を引くための 3 つの値
export interface IdempotencyRef {
  userId: string;
  operationId: string;
  key: string;
}

// 鍵の行に書く値。ttl は作った日時の 24 時間後（UNIX 秒）
export interface IdempotencyClaim extends IdempotencyRef {
  createdId: string;
  createdAt: string;
  ttl: number;
}

export function parseIdempotencyKey(raw: unknown): string {
  if (typeof raw !== "string" || !KEY_PATTERN.test(raw)) {
    throw new InvalidRequestError("作成の冪等の鍵は 1〜64 字の英数字とハイフンです", "invalid_idempotency_key");
  }
  return raw;
}

export const idempotencyPk = (ref: IdempotencyRef) => `IDEMP#${ref.userId}#${ref.operationId}#${ref.key}`;

export const epochSeconds = (iso: string) => Math.floor(Date.parse(iso) / 1000);

export function claimOf(ref: IdempotencyRef, createdId: string, createdAt: string): IdempotencyClaim {
  return { ...ref, createdId, createdAt, ttl: epochSeconds(createdAt) + IDEMPOTENCY_TTL_SECONDS };
}

// 期限は TTL の削除に頼らず、読むたびに今の時刻と比べる（DynamoDB の TTL は期限を過ぎた行をすぐには消さない）
export function liveCreatedId(row: { createdId: string; ttl: number } | null, nowIso: string): string | null {
  if (!row) return null;
  return row.ttl > epochSeconds(nowIso) ? row.createdId : null;
}
