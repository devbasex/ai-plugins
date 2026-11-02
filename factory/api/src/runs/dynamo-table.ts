// 実行の表の本物（DynamoDB の 1 つの表 RUNS_TABLE_NAME）。行の形は #2186・#2347 の設計の「データ構造」。
// 枠と実行の増減は 1 回のトランザクションで書き、条件で失敗したら書かない（I34）。開発ループ・持ち分・手元のランナーの枠も
// 同じトランザクションで書く（#2347 の決定 2・11・12）
import { DynamoDBClient, TransactionCanceledException } from "@aws-sdk/client-dynamodb";
import {
  DeleteCommand,
  DynamoDBDocumentClient,
  GetCommand,
  PutCommand,
  QueryCommand,
  ScanCommand,
  TransactWriteCommand,
  UpdateCommand,
} from "@aws-sdk/lib-dynamodb";
import type {
  CreateRunGuards,
  CreateRunResult,
  CredentialRow,
  DevLoop,
  FailureReason,
  LoopWrite,
  Registration,
  RunJob,
  RunState,
  Slots,
  SprintRun,
} from "./model.ts";
import { isTerminalJob, milestoneHash } from "./model.ts";
import type { RunsTable, RunTransitionExtras } from "./table.ts";

export const GSI1 = "gsi1";
const ACTIVE_JOBS = "ACTIVE_JOBS";
const TERMINAL = ["done", "stopped", "failed", "cancelled"];

const accountPk = (a: string) => `ACCOUNT#${a}`;
const runPk = (r: string) => `RUN#${r}`;
const jobPk = (j: string) => `JOB#${j}`;
const repoSk = (r: number) => `REPO#${r}`;
const repoSlotSk = (r: number) => `ACTIVE#REPO#${r}`;
const loopSk = (l: string) => `LOOP#${l}`;
// LOOP# で始めない（listLoops の前方一致に入れない）
const loopRunSk = (l: string, r: string) => `LOOPRUN#${l}#${r}`;
const milestoneSk = (r: number, title: string) => `MILESTONE#${r}#${milestoneHash(title)}`;
const issueSk = (r: number, n: number) => `ISSUE#${r}#${n}`;
const runnerSk = (id: string) => `RUNNER#${id}`;
const WAITING_LOOPS = "WAITING_LOOPS";
const AWAITING_RESET = "AWAITING_RESET";
const MIGRATION = { pk: "MIGRATION", sk: "MIGRATED#2347" };
const SLOT_RETRIES = 3;

// トランザクションの何番目の項目が条件で落ちたか
const failedIndexes = (err: unknown): number[] =>
  err instanceof TransactionCanceledException
    ? (err.CancellationReasons ?? []).flatMap((r, i) => (r.Code === "ConditionalCheckFailed" ? [i] : []))
    : [];

const waitingOf = (state: unknown, waitReason: unknown) => state === "between_laps" && waitReason !== "user_start";

type Item = Record<string, unknown>;
const strip = <T>(item: Item | undefined): T | null => {
  if (!item) return null;
  const { pk: _pk, sk: _sk, gsi1pk: _g1, gsi1sk: _g2, ...rest } = item;
  return rest as T;
};

const isConditionFailure = (err: unknown) =>
  (err instanceof TransactionCanceledException && err.CancellationReasons?.some((r) => r.Code === "ConditionalCheckFailed")) ||
  (err as { name?: string }).name === "ConditionalCheckFailedException";

export class DynamoRunsTable implements RunsTable {
  private readonly doc: DynamoDBDocumentClient;

  constructor(
    private readonly tableName: string,
    client: DynamoDBClient = new DynamoDBClient({}),
  ) {
    this.doc = DynamoDBDocumentClient.from(client, { marshallOptions: { removeUndefinedValues: true } });
  }

  private async get<T>(pk: string, sk: string): Promise<T | null> {
    const out = await this.doc.send(new GetCommand({ TableName: this.tableName, Key: { pk, sk }, ConsistentRead: true }));
    return strip<T>(out.Item);
  }

  private async attempt(run: () => Promise<unknown>): Promise<boolean> {
    try {
      await run();
      return true;
    } catch (err) {
      if (isConditionFailure(err)) return false;
      throw err;
    }
  }

  getRegistration(accountId: string, repositoryId: number) {
    return this.get<Registration>(accountPk(accountId), repoSk(repositoryId));
  }

  async listRegistrations(accountId: string): Promise<Registration[]> {
    const out = await this.doc.send(
      new QueryCommand({
        TableName: this.tableName,
        KeyConditionExpression: "pk = :pk AND begins_with(sk, :p)",
        ExpressionAttributeValues: { ":pk": accountPk(accountId), ":p": "REPO#" },
        ConsistentRead: true,
      }),
    );
    return (out.Items ?? []).map((i) => strip<Registration>(i)!);
  }

  putRegistration(r: Registration) {
    return this.attempt(() =>
      this.doc.send(
        new PutCommand({
          TableName: this.tableName,
          Item: { pk: accountPk(r.accountId), sk: repoSk(r.repositoryId), ...r },
          ConditionExpression: "attribute_not_exists(pk)",
        }),
      ),
    );
  }

  async setRegistrationName(accountId: string, repositoryId: number, fullName: string) {
    await this.attempt(() =>
      this.doc.send(
        new UpdateCommand({
          TableName: this.tableName,
          Key: { pk: accountPk(accountId), sk: repoSk(repositoryId) },
          UpdateExpression: "SET fullName = :n",
          ConditionExpression: "attribute_exists(pk)",
          ExpressionAttributeValues: { ":n": fullName },
        }),
      ),
    );
  }

  async deleteRegistration(accountId: string, repositoryId: number) {
    if (!(await this.getRegistration(accountId, repositoryId))) return "not_found" as const;
    const ok = await this.attempt(() =>
      this.doc.send(
        new TransactWriteCommand({
          TransactItems: [
            {
              ConditionCheck: {
                TableName: this.tableName,
                Key: { pk: accountPk(accountId), sk: repoSlotSk(repositoryId) },
                ConditionExpression: "attribute_not_exists(pk) OR #c = :zero",
                ExpressionAttributeNames: { "#c": "count" },
                ExpressionAttributeValues: { ":zero": 0 },
              },
            },
            { Delete: { TableName: this.tableName, Key: { pk: accountPk(accountId), sk: repoSk(repositoryId) } } },
          ],
        }),
      ),
    );
    return ok ? ("deleted" as const) : ("in_use" as const);
  }

  getCredential(accountId: string) {
    return this.get<CredentialRow>(accountPk(accountId), "CREDENTIAL");
  }

  async putCredential(row: CredentialRow) {
    await this.doc.send(new PutCommand({ TableName: this.tableName, Item: { pk: accountPk(row.accountId), sk: "CREDENTIAL", ...row } }));
  }

  async deleteCredential(accountId: string) {
    await this.doc.send(new DeleteCommand({ TableName: this.tableName, Key: { pk: accountPk(accountId), sk: "CREDENTIAL" } }));
  }

  async getSlots(accountId: string, repositoryId: number): Promise<Slots> {
    const account = await this.get<{ count: number }>(accountPk(accountId), "ACTIVE");
    const repository = await this.get<{ count: number; legacyCount?: number }>(accountPk(accountId), repoSlotSk(repositoryId));
    return { account: account?.count ?? 0, repository: repository?.count ?? 0, legacy: repository?.legacyCount ?? 0 };
  }

  private slotPut(pk: string, sk: string, expected: number) {
    return {
      Update: {
        TableName: this.tableName,
        Key: { pk, sk },
        UpdateExpression: "SET #c = :next",
        ConditionExpression: expected === 0 ? "attribute_not_exists(pk) OR #c = :zero" : "#c = :expected",
        ExpressionAttributeNames: { "#c": "count" },
        ExpressionAttributeValues: expected === 0 ? { ":next": 1, ":zero": 0 } : { ":next": expected + 1, ":expected": expected },
      },
    };
  }

  private jobItem(job: RunJob): Item {
    const active = !isTerminalJob(job.state);
    return {
      pk: jobPk(job.jobId),
      sk: "JOB",
      ...job,
      ...(active ? { gsi1pk: ACTIVE_JOBS, gsi1sk: `${job.createdAt}#${job.jobId}` } : {}),
    };
  }

  async createRun(run: SprintRun, job: RunJob, expected: Slots, guards?: CreateRunGuards): Promise<CreateRunResult> {
    const repoSlot = this.slotPut(accountPk(run.accountId), repoSlotSk(run.repositoryId), expected.repository);
    if (guards?.legacy) {
      // 古い版の実行は legacyCount を数える（I48）
      repoSlot.Update.UpdateExpression += ", legacyCount = if_not_exists(legacyCount, :lz) + :lone";
      Object.assign(repoSlot.Update.ExpressionAttributeValues, { ":lz": 0, ":lone": 1 });
    } else if (guards) {
      // 新しい版の実行は、古い版の実行がリポジトリに無いときだけ書く
      repoSlot.Update.ConditionExpression = `(${repoSlot.Update.ConditionExpression}) AND (attribute_not_exists(legacyCount) OR legacyCount = :lz)`;
      Object.assign(repoSlot.Update.ExpressionAttributeValues, { ":lz": 0 });
    }
    const runItem = { pk: runPk(run.sprintRunId), sk: "RUN", gsi1pk: accountPk(run.accountId), gsi1sk: `RUN#${run.createdAt}`, ...run };
    if (guards) runItem.legacy = guards.legacy;
    const items: Record<string, unknown>[] = [
      this.slotPut(accountPk(run.accountId), "ACTIVE", expected.account),
      repoSlot,
      { Put: { TableName: this.tableName, Item: runItem, ConditionExpression: "attribute_not_exists(pk)" } },
      { Put: { TableName: this.tableName, Item: this.jobItem(job), ConditionExpression: "attribute_not_exists(pk)" } },
    ];
    const issueStart = items.length;
    for (const n of guards?.issues ?? []) {
      items.push({
        Put: {
          TableName: this.tableName,
          Item: { pk: accountPk(run.accountId), sk: issueSk(run.repositoryId, n), number: n, sprintRunId: run.sprintRunId },
          ConditionExpression: "attribute_not_exists(pk)",
        },
      });
    }
    const milestoneStart = items.length;
    for (const title of guards?.milestoneChecks ?? []) {
      items.push({
        ConditionCheck: {
          TableName: this.tableName,
          Key: { pk: accountPk(run.accountId), sk: milestoneSk(run.repositoryId, title) },
          ConditionExpression: "attribute_not_exists(pk)",
        },
      });
    }
    const loopStart = items.length;
    if (guards?.loop) items.push(...this.loopItems(guards.loop));
    // ループの実行の行（詳細がそのループの実行だけを引く）。条件を持たないため失敗の位置を数える並びの最後に置く
    if (run.loopId) {
      items.push({ Put: { TableName: this.tableName, Item: { pk: accountPk(run.accountId), sk: loopRunSk(run.loopId, run.sprintRunId), sprintRunId: run.sprintRunId } } });
    }
    try {
      await this.doc.send(new TransactWriteCommand({ TransactItems: items as never }));
      return "created";
    } catch (err) {
      const failed = failedIndexes(err);
      if (failed.length === 0 && !isConditionFailure(err)) throw err;
      if (failed.some((i) => i >= loopStart)) return "loop_changed";
      if (failed.some((i) => i >= milestoneStart)) return "issue_in_loop";
      if (failed.some((i) => i >= issueStart)) return "issue_in_use";
      return "slots";
    }
  }

  // ループの行の条件つきの書き込み（と終わりの状態への移りで消す持ち分）
  private loopItems(write: LoopWrite): Record<string, unknown>[] {
    const names: Record<string, string> = {};
    const values: Record<string, unknown> = {};
    const sets: string[] = [];
    let i = 0;
    for (const [k, v] of Object.entries(write.set)) {
      names[`#l${i}`] = k;
      values[`:l${i}`] = v ?? null;
      sets.push(`#l${i} = :l${i}`);
      i += 1;
    }
    let tail = "";
    if ("state" in write.set) {
      if (waitingOf(write.set.state, write.set.waitReason)) {
        sets.push("gsi1pk = :wpk", "gsi1sk = :wsk");
        values[":wpk"] = WAITING_LOOPS;
        values[":wsk"] = `${write.set.updatedAt ?? ""}#${write.loopId}`;
      } else {
        tail = " REMOVE gsi1pk, gsi1sk";
      }
    }
    const conds = ["attribute_exists(pk)"];
    if (write.expect.currentRunId !== undefined) {
      names["#cr"] = "currentRunId";
      if (write.expect.currentRunId === null) {
        conds.push("(attribute_not_exists(#cr) OR attribute_type(#cr, :nul))");
        values[":nul"] = "NULL";
      } else {
        conds.push("#cr = :cr");
        values[":cr"] = write.expect.currentRunId;
      }
    }
    if (write.expect.lapRuns !== undefined) {
      names["#lr"] = "lapRuns";
      conds.push("#lr = :lr");
      values[":lr"] = write.expect.lapRuns;
    }
    if (write.expect.states) {
      names["#ls"] = "state";
      const keys = write.expect.states.map((st, j) => {
        values[`:ls${j}`] = st;
        return `:ls${j}`;
      });
      conds.push(`#ls IN (${keys.join(", ")})`);
    }
    if (write.expect.answers !== undefined) {
      names["#la"] = "answers";
      conds.push("#la = :la");
      values[":la"] = write.expect.answers;
    }
    const items: Record<string, unknown>[] = [
      {
        Update: {
          TableName: this.tableName,
          Key: { pk: accountPk(write.accountId), sk: loopSk(write.loopId) },
          UpdateExpression: `SET ${sets.length > 0 ? sets.join(", ") : "#l_u = :l_u"}${tail}`,
          ConditionExpression: conds.join(" AND "),
          ExpressionAttributeNames: sets.length > 0 ? names : { ...names, "#l_u": "updatedAt" },
          ExpressionAttributeValues: sets.length > 0 ? values : { ...values, ":l_u": new Date().toISOString() },
        },
      },
    ];
    for (const title of write.releaseMilestones?.titles ?? []) {
      items.push({
        Delete: {
          TableName: this.tableName,
          Key: { pk: accountPk(write.accountId), sk: milestoneSk(write.releaseMilestones!.repositoryId, title) },
          ConditionExpression: "attribute_not_exists(pk) OR loopId = :lid",
          ExpressionAttributeValues: { ":lid": write.loopId },
        },
      });
    }
    return items;
  }

  getRun(sprintRunId: string) {
    return this.get<SprintRun>(runPk(sprintRunId), "RUN");
  }

  async listRuns(accountId: string, limit: number): Promise<SprintRun[]> {
    const out = await this.doc.send(
      new QueryCommand({
        TableName: this.tableName,
        IndexName: GSI1,
        KeyConditionExpression: "gsi1pk = :pk AND begins_with(gsi1sk, :p)",
        ExpressionAttributeValues: { ":pk": accountPk(accountId), ":p": "RUN#" },
        ScanIndexForward: false,
        Limit: limit,
      }),
    );
    // GSI は結果整合のため、行は表から読み直す
    const runs = await Promise.all((out.Items ?? []).map((i) => this.getRun(i.sprintRunId as string)));
    return runs.filter((r): r is SprintRun => r !== null);
  }

  async setCancelRequested(sprintRunId: string) {
    await this.doc.send(
      new UpdateCommand({
        TableName: this.tableName,
        Key: { pk: runPk(sprintRunId), sk: "RUN" },
        UpdateExpression: "SET cancelRequested = :t",
        ConditionExpression: "attribute_exists(pk)",
        ExpressionAttributeValues: { ":t": true },
      }),
    );
  }

  async markRunRunning(sprintRunId: string, now: string) {
    await this.attempt(() =>
      this.doc.send(
        new UpdateCommand({
          TableName: this.tableName,
          Key: { pk: runPk(sprintRunId), sk: "RUN" },
          UpdateExpression: "SET #s = :running, updatedAt = :now",
          ConditionExpression: "#s = :starting",
          ExpressionAttributeNames: { "#s": "state" },
          ExpressionAttributeValues: { ":running": "running", ":starting": "starting", ":now": now },
        }),
      ),
    );
  }

  addResumeJob(job: RunJob, now: string, opts: { from?: "awaiting_approval" | "awaiting_reset"; loop?: LoopWrite } = {}) {
    const from = opts.from ?? "awaiting_approval";
    const items: Record<string, unknown>[] = [
      {
        Update: {
          TableName: this.tableName,
          Key: { pk: runPk(job.sprintRunId), sk: "RUN" },
          UpdateExpression: "SET #s = :starting, currentJobId = :j, jobCount = :seq, updatedAt = :now, resumeAt = :nul",
          ConditionExpression: "#s = :awaiting AND attribute_type(currentJobId, :null)",
          ExpressionAttributeNames: { "#s": "state" },
          ExpressionAttributeValues: { ":starting": "starting", ":awaiting": from, ":j": job.jobId, ":seq": job.seq, ":now": now, ":null": "NULL", ":nul": null },
        },
      },
      { Put: { TableName: this.tableName, Item: this.jobItem(job), ConditionExpression: "attribute_not_exists(pk)" } },
    ];
    if (from === "awaiting_reset") items.push({ Delete: { TableName: this.tableName, Key: { pk: runPk(job.sprintRunId), sk: "RESET" } } });
    if (opts.loop) items.push(...this.loopItems(opts.loop));
    return this.attempt(() => this.doc.send(new TransactWriteCommand({ TransactItems: items as never })));
  }

  private terminalValues() {
    return { ":t1": TERMINAL[0], ":t2": TERMINAL[1], ":t3": TERMINAL[2], ":t4": TERMINAL[3] };
  }

  awaitApproval(sprintRunId: string, jobId: string, now: string, extras: RunTransitionExtras = {}) {
    const items: Record<string, unknown>[] = [
      {
        Update: {
          TableName: this.tableName,
          Key: { pk: runPk(sprintRunId), sk: "RUN" },
          UpdateExpression: `SET #s = :awaiting, currentJobId = :null, updatedAt = :now${extras.finishReason !== undefined ? ", finishReason = :fr" : ""}`,
          ConditionExpression: "currentJobId = :j AND NOT #s IN (:t1, :t2, :t3, :t4)",
          ExpressionAttributeNames: { "#s": "state" },
          ExpressionAttributeValues: {
            ":awaiting": "awaiting_approval",
            ":null": null,
            ":now": now,
            ":j": jobId,
            ...this.terminalValues(),
            ...(extras.finishReason !== undefined ? { ":fr": extras.finishReason } : {}),
          },
        },
      },
      ...(extras.loop ? this.loopItems(extras.loop) : []),
    ];
    return this.attempt(() => this.doc.send(new TransactWriteCommand({ TransactItems: items as never })));
  }

  awaitReset(sprintRunId: string, jobId: string, resumeAt: string, now: string, extras: RunTransitionExtras = {}) {
    const items: Record<string, unknown>[] = [
      {
        Update: {
          TableName: this.tableName,
          Key: { pk: runPk(sprintRunId), sk: "RUN" },
          UpdateExpression: "SET #s = :reset, currentJobId = :null, updatedAt = :now, resumeAt = :at, finishReason = :fr",
          ConditionExpression: "currentJobId = :j AND NOT #s IN (:t1, :t2, :t3, :t4)",
          ExpressionAttributeNames: { "#s": "state" },
          ExpressionAttributeValues: {
            ":reset": "awaiting_reset",
            ":null": null,
            ":now": now,
            ":at": resumeAt,
            ":fr": extras.finishReason ?? "usage_limit",
            ":j": jobId,
            ...this.terminalValues(),
          },
        },
      },
      // 見回りがリセット時刻で引く印
      { Put: { TableName: this.tableName, Item: { pk: runPk(sprintRunId), sk: "RESET", sprintRunId, gsi1pk: AWAITING_RESET, gsi1sk: `${resumeAt}#${sprintRunId}` } } },
      ...(extras.loop ? this.loopItems(extras.loop) : []),
    ];
    return this.attempt(() => this.doc.send(new TransactWriteCommand({ TransactItems: items as never })));
  }

  async listResetRuns(before: string): Promise<SprintRun[]> {
    const out = await this.doc.send(
      new QueryCommand({
        TableName: this.tableName,
        IndexName: GSI1,
        KeyConditionExpression: "gsi1pk = :pk AND gsi1sk <= :b",
        ExpressionAttributeValues: { ":pk": AWAITING_RESET, ":b": `${before}#~` },
      }),
    );
    const runs = await Promise.all((out.Items ?? []).map((i) => this.getRun(i.sprintRunId as string)));
    return runs.filter((r): r is SprintRun => r !== null && r.state === "awaiting_reset");
  }

  async finishRun(sprintRunId: string, state: RunState, reason: FailureReason | null, now: string, extras: RunTransitionExtras = {}) {
    // 読んだ後に埋め戻しが legacy を書いたときは条件で落ちる。読み直してやり直す（減算と持ち分の削除を漏らさない）
    for (let attempt = 0; attempt < SLOT_RETRIES; attempt++) {
      const run = await this.getRun(sprintRunId);
      if (!run) return false;
      const shares = (await this.listIssueShares(run.accountId, run.repositoryId)).filter((x) => x.sprintRunId === sprintRunId);
      const decrement = (sk: string, legacy = false) => ({
        Update: {
          TableName: this.tableName,
          Key: { pk: accountPk(run.accountId), sk },
          UpdateExpression: legacy ? "SET #c = #c - :one, legacyCount = legacyCount - :one" : "SET #c = #c - :one",
          ConditionExpression: legacy ? "#c > :zero AND legacyCount > :zero" : "#c > :zero",
          ExpressionAttributeNames: { "#c": "count" },
          ExpressionAttributeValues: { ":one": 1, ":zero": 0 },
        },
      });
      const legacyCond = run.legacy === true ? "legacy = :lt" : run.legacy === false ? "legacy = :lf" : "attribute_not_exists(legacy)";
      const items: Record<string, unknown>[] = [
        {
          Update: {
            TableName: this.tableName,
            Key: { pk: runPk(sprintRunId), sk: "RUN" },
            UpdateExpression: `SET #s = :state, failureReason = :reason, currentJobId = :null, updatedAt = :now, resumeAt = :null${
              extras.finishReason !== undefined ? ", finishReason = :fr" : ""
            }`,
            ConditionExpression: `NOT #s IN (:t1, :t2, :t3, :t4) AND ${legacyCond}`,
            ExpressionAttributeNames: { "#s": "state" },
            ExpressionAttributeValues: {
              ":state": state,
              ":reason": reason,
              ":null": null,
              ":now": now,
              ...this.terminalValues(),
              ...(run.legacy === true ? { ":lt": true } : run.legacy === false ? { ":lf": false } : {}),
              ...(extras.finishReason !== undefined ? { ":fr": extras.finishReason } : {}),
            },
          },
        },
        decrement("ACTIVE"),
        decrement(repoSlotSk(run.repositoryId), run.legacy === true),
        ...shares.map((x) => ({
          Delete: {
            TableName: this.tableName,
            Key: { pk: accountPk(run.accountId), sk: issueSk(run.repositoryId, x.number) },
            ConditionExpression: "sprintRunId = :sr",
            ExpressionAttributeValues: { ":sr": sprintRunId },
          },
        })),
        ...(run.state === "awaiting_reset" ? [{ Delete: { TableName: this.tableName, Key: { pk: runPk(sprintRunId), sk: "RESET" } } }] : []),
        ...(extras.loop ? this.loopItems(extras.loop) : []),
      ];
      try {
        await this.doc.send(new TransactWriteCommand({ TransactItems: items as never }));
        return true;
      } catch (err) {
        const failed = failedIndexes(err);
        if (failed.length === 0 && !isConditionFailure(err)) throw err;
        // 実行の行の条件で落ち、読み直して終わりの状態なら 2 度目の移しで何もしない
        const now2 = await this.getRun(sprintRunId);
        if (!now2 || TERMINAL.includes(now2.state)) return false;
        if (failed.some((i) => i >= 3 && i < 3 + shares.length) || (now2.legacy !== run.legacy)) continue;
        return false;
      }
    }
    return false;
  }

  getJob(jobId: string) {
    return this.get<RunJob>(jobPk(jobId), "JOB");
  }

  async listActiveJobs(): Promise<RunJob[]> {
    const out: RunJob[] = [];
    let start: Record<string, unknown> | undefined;
    do {
      const page = await this.doc.send(
        new QueryCommand({
          TableName: this.tableName,
          IndexName: GSI1,
          KeyConditionExpression: "gsi1pk = :pk",
          ExpressionAttributeValues: { ":pk": ACTIVE_JOBS },
          ExclusiveStartKey: start,
        }),
      );
      for (const i of page.Items ?? []) {
        const job = await this.getJob(i.jobId as string);
        if (job && !isTerminalJob(job.state)) out.push(job);
      }
      start = page.LastEvaluatedKey;
    } while (start);
    return out;
  }

  async updateJob(jobId: string, patch: Partial<RunJob>, from?: RunJob["state"][]) {
    const entries = Object.entries(patch).filter(([k]) => k !== "jobId");
    if (entries.length === 0) return true;
    const names: Record<string, string> = {};
    const values: Record<string, unknown> = {};
    const sets = entries.map(([k, v], i) => {
      names[`#k${i}`] = k;
      values[`:v${i}`] = v;
      return `#k${i} = :v${i}`;
    });
    // 終わりの状態へ移すときは見回りの GSI から外す
    const leaving = patch.state !== undefined && isTerminalJob(patch.state);
    // 手元のランナーが受け取ったジョブは、ランナーの枠を同じ書き込みで減らす（I50）
    const current = leaving ? await this.getJob(jobId) : null;
    if (current?.runnerId && current.state === "running") return this.finishLocalJob(current, patch, from);
    let condition = "attribute_exists(pk)";
    if (from) {
      names["#st"] = "state";
      const keys = from.map((s, i) => {
        values[`:f${i}`] = s;
        return `:f${i}`;
      });
      condition += ` AND #st IN (${keys.join(", ")})`;
    }
    return this.attempt(() =>
      this.doc.send(
        new UpdateCommand({
          TableName: this.tableName,
          Key: { pk: jobPk(jobId), sk: "JOB" },
          UpdateExpression: `SET ${sets.join(", ")}${leaving ? " REMOVE gsi1pk, gsi1sk" : ""}`,
          ConditionExpression: condition,
          ExpressionAttributeNames: names,
          ExpressionAttributeValues: values,
        }),
      ),
    );
  }

  private async finishLocalJob(job: RunJob, patch: Partial<RunJob>, from?: RunJob["state"][]): Promise<boolean> {
    const entries = Object.entries(patch).filter(([k]) => k !== "jobId");
    const names: Record<string, string> = { "#st": "state" };
    const values: Record<string, unknown> = {};
    const sets = entries.map(([k, v], i) => {
      names[`#k${i}`] = k;
      values[`:v${i}`] = v;
      return `#k${i} = :v${i}`;
    });
    const keys = (from ?? ["running"]).map((st, i) => {
      values[`:f${i}`] = st;
      return `:f${i}`;
    });
    return this.attempt(() =>
      this.doc.send(
        new TransactWriteCommand({
          TransactItems: [
            {
              Update: {
                TableName: this.tableName,
                Key: { pk: jobPk(job.jobId), sk: "JOB" },
                UpdateExpression: `SET ${sets.join(", ")} REMOVE gsi1pk, gsi1sk`,
                ConditionExpression: `attribute_exists(pk) AND #st IN (${keys.join(", ")})`,
                ExpressionAttributeNames: names,
                ExpressionAttributeValues: values,
              },
            },
            {
              Update: {
                TableName: this.tableName,
                Key: { pk: accountPk(job.accountId), sk: runnerSk(job.runnerId!) },
                UpdateExpression: "ADD #c :minus",
                ExpressionAttributeNames: { "#c": "count" },
                ExpressionAttributeValues: { ":minus": -1 },
              },
            },
          ],
        }),
      ),
    );
  }

  claimLocalJob(jobId: string, runnerId: string, maxJobs: number, now: string) {
    return this.attempt(async () => {
      const job = await this.getJob(jobId);
      if (!job) throw Object.assign(new Error("no job"), { name: "ConditionalCheckFailedException" });
      await this.doc.send(
        new TransactWriteCommand({
          TransactItems: [
            {
              Update: {
                TableName: this.tableName,
                Key: { pk: jobPk(jobId), sk: "JOB" },
                UpdateExpression: "SET #st = :running, startedAt = :now, runnerId = :rid",
                ConditionExpression: "#st = :pending AND runnerKind = :local",
                ExpressionAttributeNames: { "#st": "state" },
                ExpressionAttributeValues: { ":running": "running", ":pending": "pending", ":local": "local", ":now": now, ":rid": runnerId },
              },
            },
            {
              Update: {
                TableName: this.tableName,
                Key: { pk: accountPk(job.accountId), sk: runnerSk(runnerId) },
                UpdateExpression: "SET #c = if_not_exists(#c, :zero) + :one",
                ConditionExpression: "attribute_not_exists(pk) OR #c < :max",
                ExpressionAttributeNames: { "#c": "count" },
                ExpressionAttributeValues: { ":zero": 0, ":one": 1, ":max": maxJobs },
              },
            },
          ],
        }),
      );
    });
  }

  async setMaxConcurrentRuns(accountId: string, repositoryId: number, value: number | null) {
    return this.attempt(() =>
      this.doc.send(
        new UpdateCommand({
          TableName: this.tableName,
          Key: { pk: accountPk(accountId), sk: repoSk(repositoryId) },
          UpdateExpression: "SET maxConcurrentRuns = :v",
          ConditionExpression: "attribute_exists(pk)",
          ExpressionAttributeValues: { ":v": value },
        }),
      ),
    );
  }

  async createLoop(loop: DevLoop): Promise<"created" | { conflict: string }> {
    const items = [
      ...loop.milestones.map((title) => ({
        Put: {
          TableName: this.tableName,
          Item: { pk: accountPk(loop.accountId), sk: milestoneSk(loop.repositoryId, title), loopId: loop.loopId, title },
          ConditionExpression: "attribute_not_exists(pk)",
        },
      })),
      {
        Put: {
          TableName: this.tableName,
          Item: {
            pk: accountPk(loop.accountId),
            sk: loopSk(loop.loopId),
            ...loop,
            ...(waitingOf(loop.state, loop.waitReason) ? { gsi1pk: WAITING_LOOPS, gsi1sk: `${loop.updatedAt}#${loop.loopId}` } : {}),
          },
          ConditionExpression: "attribute_not_exists(pk)",
        },
      },
    ];
    try {
      await this.doc.send(new TransactWriteCommand({ TransactItems: items as never }));
      return "created";
    } catch (err) {
      const failed = failedIndexes(err).filter((i) => i < loop.milestones.length);
      if (failed.length === 0 && !isConditionFailure(err)) throw err;
      return { conflict: loop.milestones[failed[0] ?? 0]! };
    }
  }

  getLoop(accountId: string, loopId: string) {
    return this.get<DevLoop>(accountPk(accountId), loopSk(loopId));
  }

  private async queryPrefix<T>(pk: string, prefix: string): Promise<T[]> {
    const out: T[] = [];
    let start: Record<string, unknown> | undefined;
    do {
      const page = await this.doc.send(
        new QueryCommand({
          TableName: this.tableName,
          KeyConditionExpression: "pk = :pk AND begins_with(sk, :p)",
          ExpressionAttributeValues: { ":pk": pk, ":p": prefix },
          ConsistentRead: true,
          ExclusiveStartKey: start,
        }),
      );
      for (const i of page.Items ?? []) out.push(strip<T>(i)!);
      start = page.LastEvaluatedKey;
    } while (start);
    return out;
  }

  async listLoops(accountId: string): Promise<DevLoop[]> {
    const loops = await this.queryPrefix<DevLoop>(accountPk(accountId), "LOOP#");
    return loops.sort((a, b) => (a.createdAt < b.createdAt ? 1 : a.createdAt > b.createdAt ? -1 : 0));
  }

  async listLoopRuns(accountId: string, loopId: string): Promise<SprintRun[]> {
    const rows = await this.queryPrefix<{ sprintRunId: string }>(accountPk(accountId), `LOOPRUN#${loopId}#`);
    const runs = await Promise.all(rows.map((r) => this.getRun(r.sprintRunId)));
    return runs.filter((r): r is SprintRun => r !== null);
  }

  writeLoop(write: LoopWrite) {
    return this.attempt(() => this.doc.send(new TransactWriteCommand({ TransactItems: this.loopItems(write) as never })));
  }

  getMilestoneShare(accountId: string, repositoryId: number, title: string) {
    return this.get<{ loopId: string; title: string }>(accountPk(accountId), milestoneSk(repositoryId, title));
  }

  async listIssueShares(accountId: string, repositoryId: number) {
    const rows = await this.queryPrefix<{ number: number; sprintRunId: string }>(accountPk(accountId), `ISSUE#${repositoryId}#`);
    return rows.map((r) => ({ number: r.number, sprintRunId: r.sprintRunId })).sort((a, b) => a.number - b.number);
  }

  async listWaitingLoops(accountId?: string): Promise<DevLoop[]> {
    const out: DevLoop[] = [];
    let start: Record<string, unknown> | undefined;
    do {
      const page = await this.doc.send(
        new QueryCommand({
          TableName: this.tableName,
          IndexName: GSI1,
          KeyConditionExpression: "gsi1pk = :pk",
          ...(accountId !== undefined
            ? { FilterExpression: "accountId = :a", ExpressionAttributeValues: { ":pk": WAITING_LOOPS, ":a": accountId } }
            : { ExpressionAttributeValues: { ":pk": WAITING_LOOPS } }),
          ExclusiveStartKey: start,
        }),
      );
      for (const i of page.Items ?? []) {
        if (accountId !== undefined && i.accountId !== accountId) continue;
        const loop = await this.getLoop(i.accountId as string, i.loopId as string);
        if (loop && waitingOf(loop.state, loop.waitReason)) out.push(loop);
      }
      start = page.LastEvaluatedKey;
    } while (start);
    return out;
  }

  async isMigrated() {
    return (await this.get<Item>(MIGRATION.pk, MIGRATION.sk)) !== null;
  }

  async setMigrated() {
    await this.doc.send(new PutCommand({ TableName: this.tableName, Item: { ...MIGRATION, migratedAt: new Date().toISOString() } }));
  }

  async listUnbackfilledRuns(): Promise<SprintRun[]> {
    const out: SprintRun[] = [];
    let start: Record<string, unknown> | undefined;
    do {
      const page = await this.doc.send(
        new ScanCommand({
          TableName: this.tableName,
          FilterExpression: "sk = :run AND attribute_not_exists(legacy) AND NOT #s IN (:t1, :t2, :t3, :t4)",
          ExpressionAttributeNames: { "#s": "state" },
          ExpressionAttributeValues: { ":run": "RUN", ...this.terminalValues() },
          ExclusiveStartKey: start,
          ConsistentRead: true,
        }),
      );
      for (const i of page.Items ?? []) out.push(strip<SprintRun>(i)!);
      start = page.LastEvaluatedKey;
    } while (start);
    return out;
  }

  backfillRun(run: SprintRun, issues: number[]) {
    return this.attempt(() =>
      this.doc.send(
        new TransactWriteCommand({
          TransactItems: [
            {
              Update: {
                TableName: this.tableName,
                Key: { pk: runPk(run.sprintRunId), sk: "RUN" },
                UpdateExpression: "SET legacy = :t",
                ConditionExpression: "attribute_not_exists(legacy) AND NOT #s IN (:t1, :t2, :t3, :t4)",
                ExpressionAttributeNames: { "#s": "state" },
                ExpressionAttributeValues: { ":t": true, ...this.terminalValues() },
              },
            },
            {
              Update: {
                TableName: this.tableName,
                Key: { pk: accountPk(run.accountId), sk: repoSlotSk(run.repositoryId) },
                UpdateExpression: "SET legacyCount = if_not_exists(legacyCount, :zero) + :one",
                ExpressionAttributeValues: { ":zero": 0, ":one": 1 },
              },
            },
            ...issues.map((n) => ({
              Put: {
                TableName: this.tableName,
                Item: { pk: accountPk(run.accountId), sk: issueSk(run.repositoryId, n), number: n, sprintRunId: run.sprintRunId },
                ConditionExpression: "attribute_not_exists(pk)",
              },
            })),
          ] as never,
        }),
      ),
    );
  }
}
