// 進捗の記録の読み出し（#2186 の実行の制御が使う）。読むだけで、保存の型に書き換えを足さない
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, GetCommand, QueryCommand } from "@aws-sdk/lib-dynamodb";
import type { JobFinishedEvent, LoopLapClosedPayload, LoopStoppedPayload, UsageRecordedEvent } from "@ndf-factory/contracts";
import { finishedKey } from "./authorize.ts";
import type { MemoryProgressEventStore } from "./memory-store.ts";
import type { ProgressEventRecord } from "./store.ts";

export interface ProgressReader {
  // 実行ジョブの job.finished（鍵 <job_id>:finished）。無ければ null
  findJobFinished(sprintRunId: string, jobId: string): Promise<JobFinishedEvent | null>;
  // スプリントの実行の usage.recorded のうち、at が since 以後のものの token の和（入力と出力）
  sumTokensSince(sprintRunId: string, since: Date): Promise<number>;
  // ジョブが送った loop.lap_closed と loop.stopped（停止の後の処理が読む。#2347）
  findLoopEvents(sprintRunId: string, jobId: string): Promise<{ laps: LoopLapClosedPayload[]; stopped: LoopStoppedPayload | null }>;
  // ループの実行が送った締めた周回（ループの詳細の画面）
  listLoopLaps(sprintRunIds: string[]): Promise<LoopLapClosedPayload[]>;
  // 実行ごとの usage.recorded の費用の和と、区間の数（section.started の数）
  runSummary(sprintRunId: string): Promise<{ costUsd: number; sections: number }>;
}

type Rec = ProgressEventRecord & { payload: Record<string, unknown> };

const loopEventsOf = (records: ProgressEventRecord[], jobId: string) => {
  const own = records.filter((e) => e.job_id === jobId) as Rec[];
  const stopped = own.filter((e) => e.type === "loop.stopped").at(-1);
  return {
    laps: own.filter((e) => e.type === "loop.lap_closed").map((e) => e.payload as unknown as LoopLapClosedPayload),
    stopped: stopped ? (stopped.payload as unknown as LoopStoppedPayload) : null,
  };
};

// 同じ周回が 2 つのジョブから届いても 1 つにする（後に届いた方）
const uniqueLaps = (records: ProgressEventRecord[]) => {
  const byLap = new Map<string, LoopLapClosedPayload>();
  for (const e of records as Rec[]) {
    if (e.type !== "loop.lap_closed") continue;
    const p = e.payload as unknown as LoopLapClosedPayload;
    byLap.set(`${p.loop}#${p.lap}`, p);
  }
  return [...byLap.values()];
};

const summaryOf = (records: ProgressEventRecord[]) => {
  let costUsd = 0;
  let sections = 0;
  for (const e of records as Rec[]) {
    if (e.type === "usage.recorded") costUsd += Number(e.payload.cost_usd ?? 0);
    if (e.type === "section.started") sections += 1;
  }
  return { costUsd: Math.round(costUsd * 1e6) / 1e6, sections };
};

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
    async findLoopEvents(sprintRunId, jobId) {
      return loopEventsOf(store.listRun(sprintRunId), jobId);
    },
    async listLoopLaps(sprintRunIds) {
      return uniqueLaps(sprintRunIds.flatMap((id) => store.listRun(id)));
    },
    async runSummary(sprintRunId) {
      return summaryOf(store.listRun(sprintRunId));
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

  // 実行の事象のうち、種類が types のもの
  private async listTypes(sprintRunId: string, types: string[]): Promise<ProgressEventRecord[]> {
    const out: ProgressEventRecord[] = [];
    let start: Record<string, unknown> | undefined;
    const values: Record<string, unknown> = { ":r": sprintRunId };
    const keys = types.map((t, i) => {
      values[`:t${i}`] = t;
      return `:t${i}`;
    });
    do {
      const page = await this.doc.send(
        new QueryCommand({
          TableName: this.eventsTable,
          KeyConditionExpression: "sprint_run_id = :r",
          FilterExpression: `#t IN (${keys.join(", ")})`,
          ExpressionAttributeNames: { "#t": "type" },
          ExpressionAttributeValues: values,
          ExclusiveStartKey: start,
          ConsistentRead: true,
        }),
      );
      out.push(...((page.Items ?? []) as ProgressEventRecord[]));
      start = page.LastEvaluatedKey;
    } while (start);
    return out;
  }

  async findLoopEvents(sprintRunId: string, jobId: string) {
    return loopEventsOf(await this.listTypes(sprintRunId, ["loop.lap_closed", "loop.stopped"]), jobId);
  }

  async listLoopLaps(sprintRunIds: string[]) {
    const all = await Promise.all(sprintRunIds.map((id) => this.listTypes(id, ["loop.lap_closed"])));
    return uniqueLaps(all.flat());
  }

  async runSummary(sprintRunId: string) {
    return summaryOf(await this.listTypes(sprintRunId, ["usage.recorded", "section.started"]));
  }
}
