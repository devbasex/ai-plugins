// 進捗の記録の読み出し（#2186 の実行の制御が使う）。読むだけで、保存の型に書き換えを足さない
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, GetCommand, QueryCommand } from "@aws-sdk/lib-dynamodb";
import type {
  GateAnsweredPayload,
  GateReachedPayload,
  JobFinishedEvent,
  LoopLapClosedPayload,
  LoopStoppedPayload,
  ReleasePresentedPayload,
  ReleaseProgressPayload,
  ReleaseRequestedPayload,
  ReleaseSettledPayload,
  StepRecordedEvent,
  UsageRecordedEvent,
} from "@ndf-factory/contracts";
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
  // ループの詳細の画面: 実行が送った締めた周回と、実行ごとの usage.recorded の費用の和・区間の数（section.started の数）。
  // 実行ごとに事象を 1 回だけ読む
  loopRunsDetail(sprintRunIds: string[]): Promise<LoopRunsDetail>;
  // 実行が送った配布の事象（#2439）。jobId を渡すとそのジョブの分だけ
  findReleaseEvents(sprintRunId: string, jobId?: string): Promise<ReleaseEvents>;
  // 実行の最後の gate.reached（一覧の承認の依頼の要約。#2439）。無ければ null
  findLastGate(sprintRunId: string): Promise<{ at: string; jobId: string | null; payload: GateReachedPayload } | null>;
  // 承認の依頼への答え（gate.answered。#2439 の配布のまとめが読む）。無ければ null
  findGateAnswer(sprintRunId: string, approvalRequestId: string): Promise<GateAnsweredPayload | null>;
  // 実行の gate.reached と gate.answered を order_key の順に（承認待ちの一覧の判定 awaitingGateOf が読む。#2430）
  listGateEvents(sprintRunId: string): Promise<ProgressEventRecord[]>;
  // ---- スプリントの一覧と詳細（#2172）。どれも本文（step.recorded の files・decisions・output）を返さない ----
  // 実行の事象を order_key の順に。step.recorded は新しい方から stepLimit 件までで、ほかの種類はすべて（I11）
  listRunEvents(sprintRunId: string, stepLimit: number): Promise<RunEvents>;
  // 最後の phase.changed・最後の step.recorded・最後の事象（一覧の行に足す 4 項目のもと）
  latestOfRun(sprintRunId: string): Promise<RunLatest>;
  // 冪等の鍵が指す、その実行の step.recorded（本文を含む）。別の実行の鍵・別の種類は null
  getStep(sprintRunId: string, idempotencyKey: string): Promise<StepRecord | null>;
  // payload.step が step の step.recorded（order_key の順）
  listStepsNamed(sprintRunId: string, step: string): Promise<StepRecord[]>;
}

export type StepRecord = StepRecordedEvent & ProgressEventRecord;

export interface RunEvents {
  events: ProgressEventRecord[];
  // 上限で読まなかった step.recorded の数
  omittedSteps: number;
}

export interface RunLatest {
  phase: ProgressEventRecord | null;
  step: StepRecord | null;
  last: ProgressEventRecord | null;
}

// 工程の中身の本文。詳細の読み直しで返さない（決定 10）
const STEP_BODY = ["files", "files_omitted", "commit_range", "decisions", "output"] as const;

const isStep = (e: ProgressEventRecord): e is StepRecord => e.type === "step.recorded";

// 本文を外した写し。外したことは has_details で分かるよう、持っていた項目の名前を残す
export function withoutBody<T extends ProgressEventRecord>(e: T): T & { body_fields?: string[] } {
  if (!isStep(e)) return e;
  const payload = { ...e.payload } as Record<string, unknown>;
  const present = STEP_BODY.filter((k) => payload[k] !== undefined);
  for (const k of STEP_BODY) delete payload[k];
  return { ...e, payload, ...(present.length > 0 ? { body_fields: present } : {}) } as T & { body_fields?: string[] };
}

// order_key の順の列から、古い step.recorded を上限の外として捨てる
export function capSteps(ordered: ProgressEventRecord[], stepLimit: number): RunEvents {
  const total = ordered.filter(isStep).length;
  let skip = Math.max(0, total - stepLimit);
  const omittedSteps = skip;
  const events: ProgressEventRecord[] = [];
  for (const e of ordered) {
    if (isStep(e) && skip > 0) {
      skip -= 1;
      continue;
    }
    events.push(withoutBody(e));
  }
  return { events, omittedSteps };
}

// order_key の順の列の最後の phase.changed・step.recorded・事象
export function latestOf(ordered: ProgressEventRecord[]): RunLatest {
  const step = ordered.filter(isStep).at(-1);
  return {
    phase: ordered.filter((e) => e.type === "phase.changed").at(-1) ?? null,
    step: step ? withoutBody(step) : null,
    last: ordered.at(-1) ?? null,
  };
}

const byOrderKey = (a: ProgressEventRecord, b: ProgressEventRecord) => (a.order_key < b.order_key ? -1 : a.order_key > b.order_key ? 1 : 0);

export interface ReleaseEvents {
  requested: ReleaseRequestedPayload[];
  presented: ReleasePresentedPayload[];
  progress: ReleaseProgressPayload[];
  settled: ReleaseSettledPayload | null;
}

const RELEASE_TYPES = ["release.requested", "release.presented", "release.progress", "release.settled"];
const GATE_TYPES = ["gate.reached", "gate.answered"];

const releaseEventsOf = (records: ProgressEventRecord[], jobId?: string): ReleaseEvents => {
  const own = (records as Rec[]).filter((e) => jobId === undefined || e.job_id === jobId);
  const of = <T>(type: string) => own.filter((e) => e.type === type).map((e) => e.payload as unknown as T);
  return {
    requested: of<ReleaseRequestedPayload>("release.requested"),
    presented: of<ReleasePresentedPayload>("release.presented"),
    progress: of<ReleaseProgressPayload>("release.progress"),
    settled: of<ReleaseSettledPayload>("release.settled").at(-1) ?? null,
  };
};

const lastGateOf = (records: ProgressEventRecord[]) => {
  const e = (records as Rec[]).filter((x) => x.type === "gate.reached").at(-1);
  return e ? { at: e.at, jobId: e.job_id, payload: e.payload as unknown as GateReachedPayload } : null;
};

const gateAnswerOf = (records: ProgressEventRecord[], approvalRequestId: string) => {
  const e = (records as Rec[]).filter((x) => x.type === "gate.answered" && x.payload.approval_request_id === approvalRequestId).at(-1);
  return e ? (e.payload as unknown as GateAnsweredPayload) : null;
};

export interface LoopRunsDetail {
  laps: LoopLapClosedPayload[];
  summaries: Record<string, { costUsd: number; sections: number }>;
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

const detailOf = (perRun: [string, ProgressEventRecord[]][]): LoopRunsDetail => ({
  laps: uniqueLaps(perRun.flatMap(([, records]) => records)),
  summaries: Object.fromEntries(perRun.map(([id, records]) => [id, summaryOf(records)])),
});

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
    async loopRunsDetail(sprintRunIds) {
      return detailOf(sprintRunIds.map((id) => [id, store.listRun(id)]));
    },
    async findReleaseEvents(sprintRunId, jobId) {
      return releaseEventsOf(store.listRun(sprintRunId), jobId);
    },
    async findLastGate(sprintRunId) {
      return lastGateOf(store.listRun(sprintRunId));
    },
    async findGateAnswer(sprintRunId, approvalRequestId) {
      return gateAnswerOf(store.listRun(sprintRunId), approvalRequestId);
    },
    async listGateEvents(sprintRunId) {
      return store.listRun(sprintRunId).filter((e) => GATE_TYPES.includes(e.type));
    },
    async listRunEvents(sprintRunId, stepLimit) {
      return capSteps(store.listRun(sprintRunId), stepLimit);
    },
    async latestOfRun(sprintRunId) {
      return latestOf(store.listRun(sprintRunId));
    },
    async getStep(sprintRunId, idempotencyKey) {
      const found = store.listRun(sprintRunId).find((e) => e.idempotency_key === idempotencyKey);
      return found && isStep(found) ? structuredClone(found) : null;
    },
    async listStepsNamed(sprintRunId, step) {
      return store
        .listRun(sprintRunId)
        .filter(isStep)
        .filter((e) => e.payload.step === step)
        .map(withoutBody);
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

  async loopRunsDetail(sprintRunIds: string[]) {
    const perRun = await Promise.all(
      sprintRunIds.map(async (id): Promise<[string, ProgressEventRecord[]]> => [id, await this.listTypes(id, ["loop.lap_closed", "usage.recorded", "section.started"])]),
    );
    return detailOf(perRun);
  }

  async findReleaseEvents(sprintRunId: string, jobId?: string) {
    return releaseEventsOf(await this.listTypes(sprintRunId, RELEASE_TYPES), jobId);
  }

  async findLastGate(sprintRunId: string) {
    return lastGateOf(await this.listTypes(sprintRunId, ["gate.reached"]));
  }

  async findGateAnswer(sprintRunId: string, approvalRequestId: string) {
    return gateAnswerOf(await this.listTypes(sprintRunId, GATE_TYPES), approvalRequestId);
  }

  async listGateEvents(sprintRunId: string): Promise<ProgressEventRecord[]> {
    return (await this.listTypes(sprintRunId, GATE_TYPES)).sort(byOrderKey);
  }

  // 表に type の索引が無いため、実行の事象を全ページ読んでから手元で古い step.recorded を捨てる。
  // ScanIndexForward=false と Limit で切ると古い gate.reached・usage.recorded を落とすため、その読み方はしない（決定 9）
  async listRunEvents(sprintRunId: string, stepLimit: number): Promise<RunEvents> {
    const out: ProgressEventRecord[] = [];
    let start: Record<string, unknown> | undefined;
    do {
      const page = await this.doc.send(
        new QueryCommand({
          TableName: this.eventsTable,
          KeyConditionExpression: "sprint_run_id = :r",
          ExpressionAttributeValues: { ":r": sprintRunId },
          ExclusiveStartKey: start,
          ConsistentRead: true,
        }),
      );
      // 本文はページごとに外し、手元に持つ量を抑える
      out.push(...((page.Items ?? []) as ProgressEventRecord[]).map(withoutBody));
      start = page.LastEvaluatedKey;
    } while (start);
    return capSteps(out.sort(byOrderKey), stepLimit);
  }

  // 新しい方から読み、最後の phase.changed と step.recorded がそろったら止める
  async latestOfRun(sprintRunId: string): Promise<RunLatest> {
    const latest: RunLatest = { phase: null, step: null, last: null };
    let start: Record<string, unknown> | undefined;
    do {
      const page = await this.doc.send(
        new QueryCommand({
          TableName: this.eventsTable,
          KeyConditionExpression: "sprint_run_id = :r",
          ExpressionAttributeValues: { ":r": sprintRunId },
          ScanIndexForward: false,
          ExclusiveStartKey: start,
          ConsistentRead: true,
        }),
      );
      for (const e of (page.Items ?? []) as ProgressEventRecord[]) {
        latest.last ??= e;
        if (!latest.phase && e.type === "phase.changed") latest.phase = e;
        if (!latest.step && isStep(e)) latest.step = withoutBody(e);
      }
      start = latest.phase && latest.step ? undefined : page.LastEvaluatedKey;
    } while (start);
    return latest;
  }

  async getStep(sprintRunId: string, idempotencyKey: string): Promise<StepRecord | null> {
    const key = await this.doc.send(new GetCommand({ TableName: this.idempotencyTable, Key: { idempotency_key: idempotencyKey }, ConsistentRead: true }));
    const orderKey = key.Item?.order_key as string | undefined;
    if (!orderKey || key.Item?.sprint_run_id !== sprintRunId) return null;
    const out = await this.doc.send(
      new GetCommand({ TableName: this.eventsTable, Key: { sprint_run_id: sprintRunId, order_key: orderKey }, ConsistentRead: true }),
    );
    const item = out.Item as ProgressEventRecord | undefined;
    return item && isStep(item) ? item : null;
  }

  async listStepsNamed(sprintRunId: string, step: string): Promise<StepRecord[]> {
    return (await this.listTypes(sprintRunId, ["step.recorded"]))
      .filter(isStep)
      .filter((e) => e.payload.step === step)
      .sort(byOrderKey)
      .map(withoutBody);
  }
}
