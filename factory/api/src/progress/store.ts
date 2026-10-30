// 進捗の記録の保存（決定 6、I10）。事象の表と冪等の鍵の表へ 1 回のトランザクションで追記する。
// 書き換え・削除の操作は持たない
import { DynamoDBClient, TransactionCanceledException } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, QueryCommand, TransactWriteCommand } from "@aws-sdk/lib-dynamodb";
import type { ProgressEvent } from "@ndf-factory/contracts";

// 事象の表の 1 行。確定仕様の「進捗の正本」の列に、受け口が作る order_key と pr_ref を足したもの
export type ProgressEventRecord = ProgressEvent & { order_key: string; pr_ref?: string };

export const PR_REF_INDEX = "pr_ref-index";

export interface ProgressEventStore {
  // 同じ冪等の鍵が無いときだけ書く。既にあれば書かずに { written: false }
  append(record: ProgressEventRecord): Promise<{ written: boolean }>;
  // Pull Request の参照（<owner/name の小文字>#<番号>）を持つ事象のスプリントの実行（重複を除く）
  findSprintRunsByPrRef(prRef: string): Promise<string[]>;
}

export class DynamoProgressEventStore implements ProgressEventStore {
  private readonly doc: DynamoDBDocumentClient;

  constructor(
    private readonly eventsTable: string,
    private readonly idempotencyTable: string,
    client: DynamoDBClient = new DynamoDBClient({}),
  ) {
    this.doc = DynamoDBDocumentClient.from(client, { marshallOptions: { removeUndefinedValues: true } });
  }

  async append(record: ProgressEventRecord): Promise<{ written: boolean }> {
    try {
      await this.doc.send(
        new TransactWriteCommand({
          TransactItems: [
            {
              Put: {
                TableName: this.idempotencyTable,
                Item: {
                  idempotency_key: record.idempotency_key,
                  sprint_run_id: record.sprint_run_id,
                  order_key: record.order_key,
                },
                ConditionExpression: "attribute_not_exists(idempotency_key)",
              },
            },
            {
              Put: {
                TableName: this.eventsTable,
                Item: record,
                ConditionExpression: "attribute_not_exists(order_key)",
              },
            },
          ],
        }),
      );
      return { written: true };
    } catch (err) {
      // 冪等の鍵の条件で取り消された分だけを重複として読む。ほかの理由（TransactionConflict など）は投げて 503 にする
      if (err instanceof TransactionCanceledException && err.CancellationReasons?.[0]?.Code === "ConditionalCheckFailed") {
        return { written: false };
      }
      throw err;
    }
  }

  async findSprintRunsByPrRef(prRef: string): Promise<string[]> {
    const runs = new Set<string>();
    let startKey: Record<string, unknown> | undefined;
    do {
      const out = await this.doc.send(
        new QueryCommand({
          TableName: this.eventsTable,
          IndexName: PR_REF_INDEX,
          KeyConditionExpression: "pr_ref = :p",
          ExpressionAttributeValues: { ":p": prRef },
          ExclusiveStartKey: startKey,
        }),
      );
      for (const item of out.Items ?? []) {
        if (typeof item.sprint_run_id === "string") runs.add(item.sprint_run_id);
      }
      startKey = out.LastEvaluatedKey;
    } while (startKey && runs.size < 2);
    return [...runs];
  }
}
