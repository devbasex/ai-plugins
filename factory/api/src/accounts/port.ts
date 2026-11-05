// アカウントの保存の契約（AccountStore）と、その引数・失敗の型。DynamoDB とメモリの実装が満たす。AWS SDK を読まない
import type { AccountRecord, HistoryRecord, SeatRecord, UserRecord } from "./model.ts";
import type { IdempotencyClaim, IdempotencyRef } from "../idempotency.ts";

// 条件つきの書き込みが条件で失敗した。呼ぶ側が意味（I12・I15・版の競合など）へ読み替える
export class ConditionFailedError extends Error {
  constructor() {
    super("condition failed");
    this.name = "ConditionFailedError";
  }
}

export interface AccountPatch {
  plan?: AccountRecord["plan"];
  executionMode?: AccountRecord["executionMode"];
}

// アカウントの更新の 1 回分。version が expectedVersion と等しいときだけ patch を当て、version を 1 増やす
export interface AccountUpdate {
  accountId: string;
  expectedVersion: number;
  patch: AccountPatch;
  updatedAt: string;
  history: HistoryRecord[];
}

// アカウントの表への読み書き。どの書き込みも履歴の行を同じトランザクションで足す（I20）
export interface AccountStore {
  getUser(userId: string): Promise<UserRecord | null>;
  // 利用者の行が無いときだけ、利用者・個人のアカウント・履歴を書く（I12）
  createUserWithPersonalAccount(user: UserRecord, account: AccountRecord, history: HistoryRecord[]): Promise<void>;
  getAccount(accountId: string): Promise<AccountRecord | null>;
  getSeat(accountId: string, userId: string): Promise<SeatRecord | null>;
  listSeats(accountId: string): Promise<SeatRecord[]>;
  // 利用者が席を持つチームのアカウントの ID（GSI gsi1）。GSI は結果整合のため、
  // 返すのは候補であり、呼ぶ側は getSeat で元の席を確かめてから使う
  listMemberships(userId: string): Promise<string[]>;
  // idempotency があれば作成の冪等の鍵の行も同じトランザクションで書く。期限の中の同じ鍵があれば ConditionFailedError（#2384 の I14）
  createTeam(account: AccountRecord, ownerSeat: SeatRecord, history: HistoryRecord[], idempotency?: IdempotencyClaim): Promise<void>;
  // 作成の冪等の鍵の行が作った識別子。期限（ttl）を過ぎた行は、残っていても無いものとして扱う
  getIdempotent(ref: IdempotencyRef, now: string): Promise<string | null>;
  // 席が無いときだけ足す（I15）
  addSeat(seat: SeatRecord, history: HistoryRecord[]): Promise<void>;
  // 席が読んだときのまま（種類と足した時刻が expected と等しい）のときだけ変える
  updateSeat(expected: SeatRecord, seatType: SeatRecord["seatType"], history: HistoryRecord[]): Promise<void>;
  // 席が読んだときのまま（種類と足した時刻が expected と等しい）のときだけ外す
  removeSeat(expected: SeatRecord, history: HistoryRecord[]): Promise<void>;
  // version が expectedVersion と等しいときだけ変え、version を 1 増やす
  updateAccount(update: AccountUpdate): Promise<void>;
}
