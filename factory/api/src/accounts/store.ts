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
import { ConditionFailedError, type AccountPatch, type AccountStore, type AccountUpdate } from "./port.ts";
import { epochSeconds, idempotencyPk, liveCreatedId, type IdempotencyClaim, type IdempotencyRef } from "../idempotency.ts";

// 鍵の頭。表の pk と GSI gsi1 の鍵は、利用者とアカウントの ID にこの頭を付ける
const USER_PREFIX = "USER#";
const ACCOUNT_PREFIX = "ACCOUNT#";
const userPk = (userId: string) => `${USER_PREFIX}${userId}`;
const accountPk = (accountId: string) => `${ACCOUNT_PREFIX}${accountId}`;

const userKey = (userId: string) => ({ pk: userPk(userId), sk: "USER" });
const accountKey = (accountId: string) => ({ pk: accountPk(accountId), sk: "ACCOUNT" });
const seatKey = (accountId: string, userId: string) => ({ pk: accountPk(accountId), sk: `SEAT#${userId}` });

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
    gsi1pk: userPk(s.userId),
    gsi1sk: accountPk(s.accountId),
    account_id: s.accountId,
    user_id: s.userId,
    seat_type: s.seatType,
    added_at: s.addedAt,
  };
}

function historyItem(h: HistoryRecord): Item {
  return {
    pk: accountPk(h.accountId),
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

// AccountPatch の項目ごとの列の名前とプレースホルダ。SET には表の並びで足す
const PATCH_COLUMNS: { field: keyof AccountPatch; column: string; placeholder: string }[] = [
  { field: "plan", column: "plan", placeholder: ":plan" },
  { field: "executionMode", column: "execution_mode", placeholder: ":mode" },
];

type TransactItem = NonNullable<TransactWriteCommandInput["TransactItems"]>[number];

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

  private put(item: Item, condition?: string): TransactItem {
    return { Put: { TableName: this.tableName, Item: item, ConditionExpression: condition } };
  }

  // どの書き込みも履歴の行を同じトランザクションで足す（I20）。並びは items → 履歴 → after
  private async transact(items: TransactItem[], history: HistoryRecord[], after: TransactItem[] = []): Promise<void> {
    const all = [...items, ...history.map((h) => this.put(historyItem(h))), ...after];
    try {
      await this.doc.send(new TransactWriteCommand({ TransactItems: all }));
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
    await this.transact([this.put(userItem(user), NOT_EXISTS), this.put(accountItem(account), NOT_EXISTS)], history);
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
      ExpressionAttributeValues: { ":pk": accountPk(accountId), ":seat": "SEAT#" },
      ConsistentRead: true,
    });
    return items.map(toSeat);
  }

  async listMemberships(userId: string) {
    const items = await this.queryAll({
      TableName: this.tableName,
      IndexName: "gsi1",
      KeyConditionExpression: "gsi1pk = :u",
      ExpressionAttributeValues: { ":u": userPk(userId) },
    });
    // 射影（KEYS_ONLY など）によらず必ず載る表の鍵 pk（ACCOUNT#<id>）から ID を取る
    return items.map((i) => (i.pk as string).slice(ACCOUNT_PREFIX.length));
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

  async createTeam(account: AccountRecord, ownerSeat: SeatRecord, history: HistoryRecord[], idempotency?: IdempotencyClaim) {
    await this.transact(
      [this.put(accountItem(account), NOT_EXISTS), this.put(seatItem(ownerSeat), NOT_EXISTS)],
      history,
      idempotency
        ? [
            {
              Put: {
                TableName: this.tableName,
                Item: { pk: idempotencyPk(idempotency), sk: "IDEMP", createdId: idempotency.createdId, createdAt: idempotency.createdAt, ttl: idempotency.ttl },
                ConditionExpression: "attribute_not_exists(pk) OR #ttl < :now",
                ExpressionAttributeNames: { "#ttl": "ttl" },
                ExpressionAttributeValues: { ":now": epochSeconds(idempotency.createdAt) },
              },
            },
          ]
        : [],
    );
  }

  async getIdempotent(ref: IdempotencyRef, now: string) {
    const item = await this.get({ pk: idempotencyPk(ref), sk: "IDEMP" });
    return liveCreatedId(item && { createdId: String(item.createdId), ttl: Number(item.ttl) }, now);
  }

  async addSeat(seat: SeatRecord, history: HistoryRecord[]) {
    await this.transact([this.put(seatItem(seat), NOT_EXISTS)], history);
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
    ], history);
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
    ], history);
  }

  async updateAccount({ accountId, expectedVersion, patch, updatedAt, history }: AccountUpdate) {
    // 予約語と重ならないよう、列の名前は常に #名前 で書く
    const names: Record<string, string> = { "#pk": "pk", "#version": "version", "#updated_at": "updated_at" };
    const sets = ["#version = :next", "#updated_at = :at"];
    const values: Record<string, unknown> = { ":expected": expectedVersion, ":next": expectedVersion + 1, ":at": updatedAt };
    for (const { field, column, placeholder } of PATCH_COLUMNS) {
      if (patch[field] === undefined) continue;
      names[`#${column}`] = column;
      sets.push(`#${column} = ${placeholder}`);
      values[placeholder] = patch[field];
    }
    await this.transact(
      [
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
      ],
      history,
    );
  }
}
