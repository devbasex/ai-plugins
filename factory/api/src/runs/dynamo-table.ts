// 実行の表の本物（DynamoDB の 1 つの表 RUNS_TABLE_NAME）。行の形は #2186 の設計の「データ構造」。
// 枠と実行の増減は 1 回のトランザクションで書き、条件で失敗したら書かない（I34）
import { DynamoDBClient, TransactionCanceledException } from "@aws-sdk/client-dynamodb";
import {
  DeleteCommand,
  DynamoDBDocumentClient,
  GetCommand,
  PutCommand,
  QueryCommand,
  TransactWriteCommand,
  UpdateCommand,
} from "@aws-sdk/lib-dynamodb";
import type { CredentialRow, FailureReason, Registration, RunJob, RunState, Slots, SprintRun } from "./model.ts";
import { isTerminalJob } from "./model.ts";
import type { RunsTable } from "./table.ts";

export const GSI1 = "gsi1";
const ACTIVE_JOBS = "ACTIVE_JOBS";
const TERMINAL = ["done", "stopped", "failed", "cancelled"];

const accountPk = (a: string) => `ACCOUNT#${a}`;
const runPk = (r: string) => `RUN#${r}`;
const jobPk = (j: string) => `JOB#${j}`;
const repoSk = (r: number) => `REPO#${r}`;
const repoSlotSk = (r: number) => `ACTIVE#REPO#${r}`;

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
    const repository = await this.get<{ count: number }>(accountPk(accountId), repoSlotSk(repositoryId));
    return { account: account?.count ?? 0, repository: repository?.count ?? 0 };
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

  createRun(run: SprintRun, job: RunJob, expected: Slots) {
    return this.attempt(() =>
      this.doc.send(
        new TransactWriteCommand({
          TransactItems: [
            this.slotPut(accountPk(run.accountId), "ACTIVE", expected.account),
            this.slotPut(accountPk(run.accountId), repoSlotSk(run.repositoryId), expected.repository),
            {
              Put: {
                TableName: this.tableName,
                Item: { pk: runPk(run.sprintRunId), sk: "RUN", gsi1pk: accountPk(run.accountId), gsi1sk: `RUN#${run.createdAt}`, ...run },
                ConditionExpression: "attribute_not_exists(pk)",
              },
            },
            { Put: { TableName: this.tableName, Item: this.jobItem(job), ConditionExpression: "attribute_not_exists(pk)" } },
          ],
        }),
      ),
    );
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

  addResumeJob(job: RunJob, now: string) {
    return this.attempt(() =>
      this.doc.send(
        new TransactWriteCommand({
          TransactItems: [
            {
              Update: {
                TableName: this.tableName,
                Key: { pk: runPk(job.sprintRunId), sk: "RUN" },
                UpdateExpression: "SET #s = :starting, currentJobId = :j, jobCount = :seq, updatedAt = :now",
                ConditionExpression: "#s = :awaiting AND attribute_type(currentJobId, :null)",
                ExpressionAttributeNames: { "#s": "state" },
                ExpressionAttributeValues: { ":starting": "starting", ":awaiting": "awaiting_approval", ":j": job.jobId, ":seq": job.seq, ":now": now, ":null": "NULL" },
              },
            },
            { Put: { TableName: this.tableName, Item: this.jobItem(job), ConditionExpression: "attribute_not_exists(pk)" } },
          ],
        }),
      ),
    );
  }

  awaitApproval(sprintRunId: string, jobId: string, now: string) {
    return this.attempt(() =>
      this.doc.send(
        new UpdateCommand({
          TableName: this.tableName,
          Key: { pk: runPk(sprintRunId), sk: "RUN" },
          UpdateExpression: "SET #s = :awaiting, currentJobId = :null, updatedAt = :now",
          ConditionExpression: "currentJobId = :j AND NOT #s IN (:t1, :t2, :t3, :t4)",
          ExpressionAttributeNames: { "#s": "state" },
          ExpressionAttributeValues: {
            ":awaiting": "awaiting_approval",
            ":null": null,
            ":now": now,
            ":j": jobId,
            ":t1": TERMINAL[0],
            ":t2": TERMINAL[1],
            ":t3": TERMINAL[2],
            ":t4": TERMINAL[3],
          },
        }),
      ),
    );
  }

  async finishRun(sprintRunId: string, state: RunState, reason: FailureReason | null, now: string) {
    const run = await this.getRun(sprintRunId);
    if (!run) return false;
    const decrement = (sk: string) => ({
      Update: {
        TableName: this.tableName,
        Key: { pk: accountPk(run.accountId), sk },
        UpdateExpression: "SET #c = #c - :one",
        ConditionExpression: "#c > :zero",
        ExpressionAttributeNames: { "#c": "count" },
        ExpressionAttributeValues: { ":one": 1, ":zero": 0 },
      },
    });
    return this.attempt(() =>
      this.doc.send(
        new TransactWriteCommand({
          TransactItems: [
            {
              Update: {
                TableName: this.tableName,
                Key: { pk: runPk(sprintRunId), sk: "RUN" },
                UpdateExpression: "SET #s = :state, failureReason = :reason, currentJobId = :null, updatedAt = :now",
                ConditionExpression: "NOT #s IN (:t1, :t2, :t3, :t4)",
                ExpressionAttributeNames: { "#s": "state" },
                ExpressionAttributeValues: {
                  ":state": state,
                  ":reason": reason,
                  ":null": null,
                  ":now": now,
                  ":t1": TERMINAL[0],
                  ":t2": TERMINAL[1],
                  ":t3": TERMINAL[2],
                  ":t4": TERMINAL[3],
                },
              },
            },
            decrement("ACTIVE"),
            decrement(repoSlotSk(run.repositoryId)),
          ],
        }),
      ),
    );
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
}
