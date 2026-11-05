// 委任の資格情報の表への読み書き（#2384 の「データ構造」）。区分の鍵は credential_id だけで、利用者の一覧は GSI で引く
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, GetCommand, QueryCommand, TransactWriteCommand, UpdateCommand } from "@aws-sdk/lib-dynamodb";
import { epochSeconds, idempotencyPk, liveCreatedId, type IdempotencyClaim, type IdempotencyRef } from "../idempotency.ts";
import type { DelegationRecord } from "./model.ts";

export interface DelegationStore {
  // 強い整合で読む（取り消しを次の要求から効かせる。I2）
  get(credentialId: string): Promise<DelegationRecord | null>;
  // 行が無いときだけ書く。idempotency があれば鍵の行も同じトランザクションで書き、期限の中の同じ鍵があれば duplicate
  create(record: DelegationRecord, idempotency?: IdempotencyClaim): Promise<"created" | "duplicate">;
  // 利用者が発行した資格情報の新しい順
  listByUser(userId: string, limit: number): Promise<DelegationRecord[]>;
  // revokedAt が空のときだけ 1 度だけ書く。既に書いてあれば何もしない
  revoke(credentialId: string, at: string): Promise<void>;
  getIdempotent(ref: IdempotencyRef, now: string): Promise<string | null>;
}

// GSI の名前（インフラが作る）
export const USER_INDEX = "user_id-created_at";

type Item = Record<string, unknown>;

function toItem(r: DelegationRecord): Item {
  return {
    credential_id: r.credentialId,
    user_id: r.userId,
    idp: r.idp,
    client_name: r.clientName,
    scope: r.scope,
    secret_hash: r.secretHash,
    created_at: r.createdAt,
    expires_at: r.expiresAt,
    ...(r.revokedAt === null ? {} : { revoked_at: r.revokedAt }),
  };
}

function toRecord(i: Item): DelegationRecord {
  return {
    credentialId: String(i.credential_id),
    userId: String(i.user_id),
    idp: i.idp as DelegationRecord["idp"],
    clientName: String(i.client_name),
    scope: i.scope as DelegationRecord["scope"],
    secretHash: String(i.secret_hash),
    createdAt: String(i.created_at),
    expiresAt: String(i.expires_at),
    revokedAt: typeof i.revoked_at === "string" ? i.revoked_at : null,
  };
}

const isCancelled = (err: unknown) => err instanceof Error && err.name === "TransactionCanceledException";
const isConditionFailed = (err: unknown) => err instanceof Error && err.name === "ConditionalCheckFailedException";

export class DynamoDelegationStore implements DelegationStore {
  private readonly doc: DynamoDBDocumentClient;

  constructor(
    private readonly tableName: string,
    client: DynamoDBClient = new DynamoDBClient({}),
  ) {
    this.doc = DynamoDBDocumentClient.from(client);
  }

  async get(credentialId: string) {
    const out = await this.doc.send(new GetCommand({ TableName: this.tableName, Key: { credential_id: credentialId }, ConsistentRead: true }));
    const item = out.Item as Item | undefined;
    return item && typeof item.secret_hash === "string" ? toRecord(item) : null;
  }

  async create(record: DelegationRecord, idempotency?: IdempotencyClaim) {
    const items: Item[] = [{ Put: { TableName: this.tableName, Item: toItem(record), ConditionExpression: "attribute_not_exists(credential_id)" } }];
    if (idempotency) {
      items.push({
        Put: {
          TableName: this.tableName,
          Item: { credential_id: idempotencyPk(idempotency), createdId: idempotency.createdId, createdAt: idempotency.createdAt, ttl: idempotency.ttl },
          ConditionExpression: "attribute_not_exists(credential_id) OR #ttl < :now",
          ExpressionAttributeNames: { "#ttl": "ttl" },
          ExpressionAttributeValues: { ":now": epochSeconds(idempotency.createdAt) },
        },
      });
    }
    try {
      await this.doc.send(new TransactWriteCommand({ TransactItems: items as never }));
      return "created" as const;
    } catch (err) {
      // 資格情報の ID は 16 バイトの乱数で重ならないため、条件の失敗は冪等の鍵の行
      if (idempotency && isCancelled(err)) return "duplicate" as const;
      throw err;
    }
  }

  async listByUser(userId: string, limit: number) {
    const out = await this.doc.send(
      new QueryCommand({
        TableName: this.tableName,
        IndexName: USER_INDEX,
        KeyConditionExpression: "user_id = :u",
        ExpressionAttributeValues: { ":u": userId },
        ScanIndexForward: false,
        Limit: limit,
      }),
    );
    return (out.Items ?? []).map((i) => toRecord(i as Item));
  }

  async revoke(credentialId: string, at: string) {
    try {
      await this.doc.send(
        new UpdateCommand({
          TableName: this.tableName,
          Key: { credential_id: credentialId },
          UpdateExpression: "SET revoked_at = :at",
          ConditionExpression: "attribute_exists(credential_id) AND attribute_not_exists(revoked_at)",
          ExpressionAttributeValues: { ":at": at },
        }),
      );
    } catch (err) {
      if (!isConditionFailed(err)) throw err;
    }
  }

  async getIdempotent(ref: IdempotencyRef, now: string) {
    const out = await this.doc.send(new GetCommand({ TableName: this.tableName, Key: { credential_id: idempotencyPk(ref) }, ConsistentRead: true }));
    const item = out.Item as Item | undefined;
    return liveCreatedId(item ? { createdId: String(item.createdId), ttl: Number(item.ttl) } : null, now);
  }
}
