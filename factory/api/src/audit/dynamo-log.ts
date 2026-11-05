// 操作の記録の表（#2384 の「データ構造」）。区分の鍵は record_id だけで、条件つきの追加だけを行う（I8）
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, PutCommand } from "@aws-sdk/lib-dynamodb";
import { toItem, type AuditLog, type AuditRecord } from "./log.ts";

export class DynamoAuditLog implements AuditLog {
  private readonly doc: DynamoDBDocumentClient;

  constructor(
    private readonly tableName: string,
    client: DynamoDBClient = new DynamoDBClient({}),
  ) {
    this.doc = DynamoDBDocumentClient.from(client, { marshallOptions: { removeUndefinedValues: true } });
  }

  async append(record: AuditRecord): Promise<void> {
    await this.doc.send(new PutCommand({ TableName: this.tableName, Item: toItem(record), ConditionExpression: "attribute_not_exists(record_id)" }));
  }
}
