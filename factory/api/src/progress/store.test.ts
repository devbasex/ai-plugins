import { beforeEach, describe, expect, expectTypeOf, it } from "vitest";
import { mockClient } from "aws-sdk-client-mock";
import { DynamoDBClient, TransactionCanceledException } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, QueryCommand, TransactWriteCommand } from "@aws-sdk/lib-dynamodb";
import { DynamoProgressEventStore, type ProgressEventRecord, type ProgressEventStore } from "./store.ts";
import { toRecord } from "./append.ts";

const ddb = mockClient(DynamoDBDocumentClient);
let store: DynamoProgressEventStore;
beforeEach(() => {
  ddb.reset();
  store = new DynamoProgressEventStore("events", "keys", new DynamoDBClient({ region: "us-east-1" }));
});

const record: ProgressEventRecord = toRecord({
  sprint_run_id: "sr-1",
  idempotency_key: "job-1:1",
  type: "step.recorded",
  at: "2026-10-30T01:02:03Z",
  source: "runner",
  job_id: "job-1",
  schema_version: 1,
  payload: { step: "pr", exit_code: 0, seconds: 1, summary: "s", pull_request: { repo: "O/R", number: 3 } },
});

const cancelled = (...codes: string[]) =>
  new TransactionCanceledException({
    message: "cancelled",
    $metadata: {},
    CancellationReasons: codes.map((Code) => ({ Code })),
  });

describe("DynamoProgressEventStore（決定 6、受け入れ条件 15・16）", () => {
  it("1 件の追記は TransactWrite 1 回で、冪等の鍵の表を先に、どちらも無いときだけの Put で書く", async () => {
    ddb.on(TransactWriteCommand).resolves({});
    expect(await store.append(record)).toEqual({ written: true });
    expect(ddb.calls()).toHaveLength(1);
    const items = ddb.commandCalls(TransactWriteCommand)[0]!.args[0].input.TransactItems!;
    expect(items.map((i) => Object.keys(i))).toEqual([["Put"], ["Put"]]);
    expect(items.map((i) => [i.Put!.TableName, i.Put!.ConditionExpression])).toEqual([
      ["keys", "attribute_not_exists(idempotency_key)"],
      ["events", "attribute_not_exists(order_key)"],
    ]);
    expect(items[0]!.Put!.Item).toEqual({ idempotency_key: "job-1:1", sprint_run_id: "sr-1", order_key: "2026-10-30T01:02:03.000Z#job-1:1" });
    expect(items[1]!.Put!.Item).toMatchObject({ sprint_run_id: "sr-1", order_key: "2026-10-30T01:02:03.000Z#job-1:1", pr_ref: "o/r#3", at: "2026-10-30T01:02:03Z" });
  });

  it("冪等の鍵の条件で取り消されたら書かずに { written: false }", async () => {
    ddb.on(TransactWriteCommand).rejects(cancelled("ConditionalCheckFailed", "None"));
    expect(await store.append(record)).toEqual({ written: false });
  });

  it("ほかの理由の取り消し・ほかの失敗は投げる（受け口が 503 にする）", async () => {
    ddb.on(TransactWriteCommand).rejects(cancelled("TransactionConflict", "None"));
    await expect(store.append(record)).rejects.toThrow(TransactionCanceledException);
    ddb.on(TransactWriteCommand).rejects(cancelled("None", "ConditionalCheckFailed"));
    await expect(store.append(record)).rejects.toThrow(TransactionCanceledException);
    ddb.on(TransactWriteCommand).rejects(new Error("throttled"));
    await expect(store.append(record)).rejects.toThrow("throttled");
  });

  it("Pull Request の参照は GSI を引き、スプリントの実行を重複なく返す", async () => {
    ddb.on(QueryCommand).resolves({ Items: [{ sprint_run_id: "sr-1" }, { sprint_run_id: "sr-1" }] });
    expect(await store.findSprintRunsByPrRef("o/r#3")).toEqual(["sr-1"]);
    expect(ddb.commandCalls(QueryCommand)[0]!.args[0].input).toMatchObject({
      TableName: "events",
      IndexName: "pr_ref-index",
      ExpressionAttributeValues: { ":p": "o/r#3" },
    });
  });

  it("保存の型に書き換えと削除の操作が無い（I10）", () => {
    expectTypeOf<keyof ProgressEventStore>().toEqualTypeOf<"append" | "findSprintRunsByPrRef">();
    expect(Object.getOwnPropertyNames(DynamoProgressEventStore.prototype).sort()).toEqual(["append", "constructor", "findSprintRunsByPrRef"]);
  });
});
