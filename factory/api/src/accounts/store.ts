import { randomUUID } from "node:crypto";
import { DynamoDBClient, TransactionCanceledException } from "@aws-sdk/client-dynamodb";
import {
  DynamoDBDocumentClient,
  GetCommand,
  QueryCommand,
  TransactWriteCommand,
  type TransactWriteCommandInput,
} from "@aws-sdk/lib-dynamodb";
import type { AccountRecord, HistoryRecord, SeatRecord, UserRecord } from "./model.ts";

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

// アカウントの表への読み書き。どの書き込みも履歴の行を同じトランザクションで足す（I20）
export interface AccountStore {
  getUser(userId: string): Promise<UserRecord | null>;
  // 利用者の行が無いときだけ、利用者・個人のアカウント・履歴を書く（I12）
  createUserWithPersonalAccount(user: UserRecord, account: AccountRecord, history: HistoryRecord[]): Promise<void>;
  getAccount(accountId: string): Promise<AccountRecord | null>;
  getSeat(accountId: string, userId: string): Promise<SeatRecord | null>;
  listSeats(accountId: string): Promise<SeatRecord[]>;
  // 利用者が席を持つチームのアカウントの ID と席の種類（GSI gsi1）。GSI は結果整合のため、
  // 返すのは候補であり、呼ぶ側は getSeat で元の席を確かめてから使う
  listMemberships(userId: string): Promise<SeatRecord[]>;
  createTeam(account: AccountRecord, ownerSeat: SeatRecord, history: HistoryRecord[]): Promise<void>;
  // 席が無いときだけ足す（I15）
  addSeat(seat: SeatRecord, history: HistoryRecord[]): Promise<void>;
  // 席が読んだときのまま（種類と足した時刻が expected と等しい）のときだけ変える
  updateSeat(expected: SeatRecord, seatType: SeatRecord["seatType"], history: HistoryRecord[]): Promise<void>;
  // 席が読んだときのまま（種類と足した時刻が expected と等しい）のときだけ外す
  removeSeat(expected: SeatRecord, history: HistoryRecord[]): Promise<void>;
  // version が expectedVersion と等しいときだけ変え、version を 1 増やす
  updateAccount(
    accountId: string,
    expectedVersion: number,
    patch: AccountPatch,
    updatedAt: string,
    history: HistoryRecord[],
  ): Promise<void>;
}

const userKey = (userId: string) => ({ pk: `USER#${userId}`, sk: "USER" });
const accountKey = (accountId: string) => ({ pk: `ACCOUNT#${accountId}`, sk: "ACCOUNT" });
const seatKey = (accountId: string, userId: string) => ({ pk: `ACCOUNT#${accountId}`, sk: `SEAT#${userId}` });

type Item = Record<string, unknown>;

function userItem(u: UserRecord): Item {
  return {
    ...userKey(u.userId),
    user_id: u.userId,
    idp: u.idp,
    personal_account_id: u.personalAccountId,
    created_at: u.createdAt,
  };
}

function accountItem(a: AccountRecord): Item {
  return {
    ...accountKey(a.accountId),
    account_id: a.accountId,
    kind: a.kind,
    plan: a.plan,
    execution_mode: a.executionMode,
    version: a.version,
    owner_user_id: a.ownerUserId,
    name: a.name,
    created_at: a.createdAt,
    updated_at: a.updatedAt,
  };
}

function seatItem(s: SeatRecord): Item {
  return {
    ...seatKey(s.accountId, s.userId),
    gsi1pk: `USER#${s.userId}`,
    gsi1sk: `ACCOUNT#${s.accountId}`,
    account_id: s.accountId,
    user_id: s.userId,
    seat_type: s.seatType,
    added_at: s.addedAt,
  };
}

function historyItem(h: HistoryRecord): Item {
  return {
    pk: `ACCOUNT#${h.accountId}`,
    sk: `HIST#${h.at}#${randomUUID()}`,
    account_id: h.accountId,
    type: h.type,
    at: h.at,
    actor: h.actor,
    before: h.before ?? null,
    after: h.after ?? null,
  };
}

function toUser(i: Item): UserRecord {
  return {
    userId: i.user_id as string,
    idp: i.idp as UserRecord["idp"],
    personalAccountId: i.personal_account_id as string,
    createdAt: i.created_at as string,
  };
}

function toAccount(i: Item): AccountRecord {
  return {
    accountId: i.account_id as string,
    kind: i.kind as AccountRecord["kind"],
    plan: i.plan as AccountRecord["plan"],
    executionMode: i.execution_mode as AccountRecord["executionMode"],
    version: i.version as number,
    ownerUserId: i.owner_user_id as string,
    name: (i.name as string | null | undefined) ?? null,
    createdAt: i.created_at as string,
    updatedAt: i.updated_at as string,
  };
}

function toSeat(i: Item): SeatRecord {
  return {
    accountId: i.account_id as string,
    userId: i.user_id as string,
    seatType: i.seat_type as SeatRecord["seatType"],
    addedAt: i.added_at as string,
  };
}

const NOT_EXISTS = "attribute_not_exists(pk)";
// 席が読んだときのまま。種類で途中の変更を、足した時刻で外して足し直したことを見分ける
const SEAT_UNCHANGED = "attribute_exists(#pk) AND #seat_type = :expected_type AND #added_at = :expected_added_at";
const SEAT_NAMES = { "#pk": "pk", "#seat_type": "seat_type", "#added_at": "added_at" };
const seatExpected = (s: SeatRecord) => ({ ":expected_type": s.seatType, ":expected_added_at": s.addedAt });

export class DynamoAccountStore implements AccountStore {
  private readonly doc: DynamoDBDocumentClient;

  constructor(
    private readonly tableName: string,
    client: DynamoDBClient = new DynamoDBClient({}),
  ) {
    this.doc = DynamoDBDocumentClient.from(client, { marshallOptions: { removeUndefinedValues: true } });
  }

  private async get(key: Item): Promise<Item | null> {
    const out = await this.doc.send(new GetCommand({ TableName: this.tableName, Key: key, ConsistentRead: true }));
    return (out.Item as Item | undefined) ?? null;
  }

  private put(item: Item, condition?: string): NonNullable<TransactWriteCommandInput["TransactItems"]>[number] {
    return { Put: { TableName: this.tableName, Item: item, ConditionExpression: condition } };
  }

  private async transact(items: NonNullable<TransactWriteCommandInput["TransactItems"]>): Promise<void> {
    try {
      await this.doc.send(new TransactWriteCommand({ TransactItems: items }));
    } catch (err) {
      if (
        err instanceof TransactionCanceledException &&
        err.CancellationReasons?.some((r) => r.Code === "ConditionalCheckFailed")
      ) {
        throw new ConditionFailedError();
      }
      throw err;
    }
  }

  async getUser(userId: string) {
    const item = await this.get(userKey(userId));
    return item && toUser(item);
  }

  async createUserWithPersonalAccount(user: UserRecord, account: AccountRecord, history: HistoryRecord[]) {
    await this.transact([
      this.put(userItem(user), NOT_EXISTS),
      this.put(accountItem(account), NOT_EXISTS),
      ...history.map((h) => this.put(historyItem(h))),
    ]);
  }

  async getAccount(accountId: string) {
    const item = await this.get(accountKey(accountId));
    return item && toAccount(item);
  }

  async getSeat(accountId: string, userId: string) {
    const item = await this.get(seatKey(accountId, userId));
    return item && toSeat(item);
  }

  async listSeats(accountId: string) {
    const items = await this.queryAll({
      TableName: this.tableName,
      KeyConditionExpression: "pk = :pk AND begins_with(sk, :seat)",
      ExpressionAttributeValues: { ":pk": `ACCOUNT#${accountId}`, ":seat": "SEAT#" },
      ConsistentRead: true,
    });
    return items.map(toSeat);
  }

  async listMemberships(userId: string) {
    const items = await this.queryAll({
      TableName: this.tableName,
      IndexName: "gsi1",
      KeyConditionExpression: "gsi1pk = :u",
      ExpressionAttributeValues: { ":u": `USER#${userId}` },
    });
    return items.map(toSeat);
  }

  private async queryAll(input: ConstructorParameters<typeof QueryCommand>[0]): Promise<Item[]> {
    const out: Item[] = [];
    let start: Record<string, unknown> | undefined;
    do {
      const page = await this.doc.send(new QueryCommand({ ...input, ExclusiveStartKey: start }));
      out.push(...((page.Items as Item[] | undefined) ?? []));
      start = page.LastEvaluatedKey;
    } while (start);
    return out;
  }

  async createTeam(account: AccountRecord, ownerSeat: SeatRecord, history: HistoryRecord[]) {
    await this.transact([
      this.put(accountItem(account), NOT_EXISTS),
      this.put(seatItem(ownerSeat), NOT_EXISTS),
      ...history.map((h) => this.put(historyItem(h))),
    ]);
  }

  async addSeat(seat: SeatRecord, history: HistoryRecord[]) {
    await this.transact([this.put(seatItem(seat), NOT_EXISTS), ...history.map((h) => this.put(historyItem(h)))]);
  }

  async updateSeat(expected: SeatRecord, seatType: SeatRecord["seatType"], history: HistoryRecord[]) {
    await this.transact([
      {
        Update: {
          TableName: this.tableName,
          Key: seatKey(expected.accountId, expected.userId),
          UpdateExpression: "SET #seat_type = :t",
          ConditionExpression: SEAT_UNCHANGED,
          ExpressionAttributeNames: SEAT_NAMES,
          ExpressionAttributeValues: { ":t": seatType, ...seatExpected(expected) },
        },
      },
      ...history.map((h) => this.put(historyItem(h))),
    ]);
  }

  async removeSeat(expected: SeatRecord, history: HistoryRecord[]) {
    await this.transact([
      {
        Delete: {
          TableName: this.tableName,
          Key: seatKey(expected.accountId, expected.userId),
          ConditionExpression: SEAT_UNCHANGED,
          ExpressionAttributeNames: SEAT_NAMES,
          ExpressionAttributeValues: seatExpected(expected),
        },
      },
      ...history.map((h) => this.put(historyItem(h))),
    ]);
  }

  async updateAccount(
    accountId: string,
    expectedVersion: number,
    patch: AccountPatch,
    updatedAt: string,
    history: HistoryRecord[],
  ) {
    // 予約語と重ならないよう、列の名前は常に #名前 で書く
    const names: Record<string, string> = { "#pk": "pk", "#version": "version", "#updated_at": "updated_at" };
    const sets = ["#version = :next", "#updated_at = :at"];
    const values: Record<string, unknown> = { ":expected": expectedVersion, ":next": expectedVersion + 1, ":at": updatedAt };
    if (patch.plan !== undefined) {
      names["#plan"] = "plan";
      sets.push("#plan = :plan");
      values[":plan"] = patch.plan;
    }
    if (patch.executionMode !== undefined) {
      names["#execution_mode"] = "execution_mode";
      sets.push("#execution_mode = :mode");
      values[":mode"] = patch.executionMode;
    }
    await this.transact([
      {
        Update: {
          TableName: this.tableName,
          Key: accountKey(accountId),
          UpdateExpression: `SET ${sets.join(", ")}`,
          ConditionExpression: "attribute_exists(#pk) AND #version = :expected",
          ExpressionAttributeNames: names,
          ExpressionAttributeValues: values,
        },
      },
      ...history.map((h) => this.put(historyItem(h))),
    ]);
  }
}
