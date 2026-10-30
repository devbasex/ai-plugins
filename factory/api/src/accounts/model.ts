// 集約「利用者」と「アカウント」の値（issues/issue-2170-design.md の「データ構造」）
export const PLANS = ["free", "pro", "max", "team"] as const;
export type Plan = (typeof PLANS)[number];

export const SEAT_TYPES = ["standard", "premium"] as const;
export type SeatType = (typeof SEAT_TYPES)[number];

export const EXECUTION_MODES = ["local", "byok", "managed"] as const;
export type ExecutionMode = (typeof EXECUTION_MODES)[number];

export type Idp = "google" | "github";
export type AccountKind = "personal" | "team";
export type Role = "owner" | "member";

export const PERSONAL_PLANS: readonly Plan[] = ["free", "pro", "max"];

// 新しいアカウントの実行の形態（I24）
export const INITIAL_EXECUTION_MODE: ExecutionMode = "local";

export interface Caller {
  userId: string;
  idp: Idp;
}

export interface UserRecord {
  userId: string;
  idp: Idp;
  personalAccountId: string;
  createdAt: string;
}

export interface AccountRecord {
  accountId: string;
  kind: AccountKind;
  plan: Plan;
  executionMode: ExecutionMode;
  version: number;
  ownerUserId: string;
  name: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface SeatRecord {
  accountId: string;
  userId: string;
  seatType: SeatType;
  addedAt: string;
}

export type HistoryType =
  | "account.created"
  | "plan.changed"
  | "seat.added"
  | "seat.changed"
  | "seat.removed"
  | "execution_mode.changed";

export interface HistoryRecord {
  accountId: string;
  type: HistoryType;
  at: string;
  actor: string;
  before: unknown;
  after: unknown;
}

// 要求を解決した結果。#2186・#2189・#2174 は実行の形態をここから読む
export interface AccountContext {
  accountId: string;
  userId: string;
  kind: AccountKind;
  plan: Plan;
  role: Role;
  seatType: SeatType | null;
  executionMode: ExecutionMode;
}

// I13: 種類とプランの組
export function planFitsKind(kind: AccountKind, plan: Plan): boolean {
  return kind === "team" ? plan === "team" : PERSONAL_PLANS.includes(plan);
}

export function isPlan(value: unknown): value is Plan {
  return typeof value === "string" && (PLANS as readonly string[]).includes(value);
}

export function isSeatType(value: unknown): value is SeatType {
  return typeof value === "string" && (SEAT_TYPES as readonly string[]).includes(value);
}

export function isExecutionMode(value: unknown): value is ExecutionMode {
  return typeof value === "string" && (EXECUTION_MODES as readonly string[]).includes(value);
}
