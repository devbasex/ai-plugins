// 委任の資格情報（#2384 の集約「委任の資格情報」）。人が画面で発行し、エージェントかクライアントへ渡す
import type { Idp } from "../accounts/model.ts";

// read は読み取りだけ、read_write は読み書き。どちらも人だけが確定できる操作は呼べない（I4）
export type DelegationScope = "read" | "read_write";
export type DelegationState = "active" | "expired" | "revoked";

export const DELEGATION_SCOPES: readonly DelegationScope[] = ["read", "read_write"];
// 期限は発行から 300〜3,600 秒（I1）
export const MIN_EXPIRES_SECONDS = 300;
export const MAX_EXPIRES_SECONDS = 3600;
export const MAX_CLIENT_NAME = 64;
// 一覧で返す数
export const LIST_LIMIT = 100;
// 値の頭。Cognito の token と分ける（決定 8）
export const CREDENTIAL_PREFIX = "ndfd_";

export interface DelegationRecord {
  credentialId: string;
  userId: string;
  idp: Idp;
  clientName: string;
  scope: DelegationScope;
  // 秘密の SHA-256 の 16 進。平文は持たない（I3）
  secretHash: string;
  createdAt: string;
  expiresAt: string;
  // null は取り消していない。取り消しで 1 度だけ書く
  revokedAt: string | null;
}

export function stateOf(record: DelegationRecord, now: string): DelegationState {
  if (record.revokedAt !== null) return "revoked";
  return record.expiresAt <= now ? "expired" : "active";
}

export function delegationView(record: DelegationRecord, now: string) {
  return {
    credential_id: record.credentialId,
    client_name: record.clientName,
    scope: record.scope,
    created_at: record.createdAt,
    expires_at: record.expiresAt,
    revoked_at: record.revokedAt,
    state: stateOf(record, now),
  };
}
