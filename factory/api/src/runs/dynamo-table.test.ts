// DynamoDB の実行の表の読み出し（#2430 の決定 2）。送った命令を偽の client で記録し、Query と読み直しの回数を数える
import { beforeEach, describe, expect, it } from "vitest";
import { mockClient } from "aws-sdk-client-mock";
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, GetCommand, QueryCommand } from "@aws-sdk/lib-dynamodb";
import { DynamoRunsTable, GSI1 } from "./dynamo-table.ts";
import type { SprintRun } from "./model.ts";

const ddb = mockClient(DynamoDBDocumentClient);
let table: DynamoRunsTable;
beforeEach(() => {
  ddb.reset();
  table = new DynamoRunsTable("runs", new DynamoDBClient({ region: "us-east-1" }));
});

function runOf(id: string, state: SprintRun["state"]): SprintRun {
  return {
    sprintRunId: id,
    accountId: "acc-1",
    repositoryId: 101,
    repo: "org-a/repo-a",
    state,
    issues: [1],
    ndfVersion: "10.27.15",
    executionMode: "byok",
    modelCredentialSecretArn: null,
    currentJobId: null,
    jobCount: 1,
    cancelRequested: false,
    failureReason: null,
    createdBy: "u-1",
    createdAt: "2026-11-04T00:00:00.000Z",
    updatedAt: "2026-11-04T00:00:00.000Z",
  };
}

describe("listAwaitingRuns（#2430 の決定 2）", () => {
  it("索引の Query 1 回に状態の条件を付け、合った実行だけを表から読み直し、読み直した状態でもう一度絞る", async () => {
    // 表の今の状態。sr-w3 は索引では承認待ちに見えるが、表では再開して running になっている
    const rows = new Map<string, SprintRun>([
      ["sr-w1", runOf("sr-w1", "awaiting_approval")],
      ["sr-w2", runOf("sr-w2", "awaiting_approval")],
      ["sr-w3", runOf("sr-w3", "running")],
    ]);
    for (let i = 0; i < 5; i += 1) rows.set(`sr-done${i}`, runOf(`sr-done${i}`, "done"));
    // 索引は状態の条件で外した終わった実行 5 件を返さない
    ddb.on(QueryCommand).resolves({ Items: ["sr-w1", "sr-w2", "sr-w3"].map((id) => ({ sprintRunId: id })) });
    ddb.on(GetCommand).callsFake((input: { Key: { pk: string } }) => {
      const run = rows.get(input.Key.pk.replace(/^RUN#/, ""));
      return { Item: run ? { pk: input.Key.pk, sk: "RUN", ...run } : undefined };
    });
    const runs = await table.listAwaitingRuns("acc-1");
    expect(runs.map((r) => r.sprintRunId)).toEqual(["sr-w1", "sr-w2"]);
    const queries = ddb.commandCalls(QueryCommand);
    expect(queries).toHaveLength(1);
    expect(queries[0]!.args[0].input).toMatchObject({
      IndexName: GSI1,
      KeyConditionExpression: "gsi1pk = :pk AND begins_with(gsi1sk, :p)",
      FilterExpression: "#s = :awaiting",
      ExpressionAttributeNames: { "#s": "state" },
      ExpressionAttributeValues: { ":pk": "ACCOUNT#acc-1", ":p": "RUN#", ":awaiting": "awaiting_approval" },
      ScanIndexForward: false,
      Limit: 1000,
    });
    expect(ddb.commandCalls(GetCommand).map((c) => c.args[0].input.Key)).toEqual([
      { pk: "RUN#sr-w1", sk: "RUN" },
      { pk: "RUN#sr-w2", sk: "RUN" },
      { pk: "RUN#sr-w3", sk: "RUN" },
    ]);
  });
});
