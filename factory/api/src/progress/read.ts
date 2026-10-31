// 進捗の記録の読み出し（#2186 の実行の制御が使う）。読むだけで、保存の型に書き換えを足さない
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, GetCommand, QueryCommand } from "@aws-sdk/lib-dynamodb";
import type { JobFinishedEvent, UsageRecordedEvent } from "@ndf-factory/contracts";
import { finishedKey } from "./authorize.ts";
import type { MemoryProgressEventStore } from "./memory-store.ts";
import type { ProgressEventRecord } from "./store.ts";

export interface ProgressReader {
  // 実行ジョブの job.finished（鍵 <job_id>:finished）。無ければ null
  findJobFinished(sprintRunId: string, jobId: string): Promise<JobFinishedEvent | null>;
  // スプリントの実行の usage.recorded のうち、at が since 以後のものの token の和（入力と出力）
  sumTokensSince(sprintRunId: string, since: Date): Promise<number>;
}

const usageTokens = (records: ProgressEventRecord[], since: Date) =>
  records
    .filter((e): e is UsageRecordedEvent & ProgressEventRecord => e.type === "usage.recorded")
    .filter((e) => new Date(e.at).getTime() >= since.getTime())
    .reduce((sum, e) => sum + e.payload.input_tokens + e.payload.output_tokens, 0);

// テストの偽の保存を読む
export function memoryProgressReader(store: MemoryProgressEventStore): ProgressReader {
  return {
    async findJobFinished(sprintRunId, jobId) {
      const key = finishedKey(jobId);
      const found = store.listRun(sprintRunId).find((e) => e.idempotency_key === key && e.type === "job.finished");
      return (found as (JobFinishedEvent & ProgressEventRecord) | undefined) ?? null;
    },
    async sumTokensSince(sprintRunId, since) {
      return usageTokens(store.listRun(sprintRunId), since);
    },
  };
}

// 事象の表（鍵 sprint_run_id・order_key）と冪等の鍵の表を読む
export class DynamoProgressReader implements ProgressReader {
  private readonly doc: DynamoDBDocumentClient;

  constructor(
    private readonly eventsTable: string,
    private readonly idempotencyTable: string,
    client: DynamoDBClient = new DynamoDBClient({}),
  ) {
    this.doc = DynamoDBDocumentClient.from(client);
  }

  async findJobFinished(sprintRunId: string, jobId: string): Promise<JobFinishedEvent | null> {
    const key = await this.doc.send(
      new GetCommand({ TableName: this.idempotencyTable, Key: { idempotency_key: finishedKey(jobId) }, ConsistentRead: true }),
    );
    const orderKey = key.Item?.order_key as string | undefined;
    if (!orderKey || key.Item?.sprint_run_id !== sprintRunId) return null;
    const out = await this.doc.send(
      new GetCommand({ TableName: this.eventsTable, Key: { sprint_run_id: sprintRunId, order_key: orderKey }, ConsistentRead: true }),
    );
    const item = out.Item as ProgressEventRecord | undefined;
    return item?.type === "job.finished" ? (item as JobFinishedEvent) : null;
  }

  async sumTokensSince(sprintRunId: string, since: Date): Promise<number> {
    let total = 0;
    let start: Record<string, unknown> | undefined;
    do {
      const out = await this.doc.send(
        new QueryCommand({
          TableName: this.eventsTable,
          KeyConditionExpression: "sprint_run_id = :r AND order_key >= :s",
          FilterExpression: "#t = :u",
          ExpressionAttributeNames: { "#t": "type" },
          ExpressionAttributeValues: { ":r": sprintRunId, ":s": since.toISOString(), ":u": "usage.recorded" },
          ExclusiveStartKey: start,
        }),
      );
      total += usageTokens((out.Items ?? []) as ProgressEventRecord[], since);
      start = out.LastEvaluatedKey;
    } while (start);
    return total;
  }
}
