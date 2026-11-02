import { beforeEach, describe, expect, it } from "vitest";
import { mockClient } from "aws-sdk-client-mock";
import { ConditionalCheckFailedException, DynamoDBClient, TransactionCanceledException } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, GetCommand, PutCommand, TransactWriteCommand } from "@aws-sdk/lib-dynamodb";
import { createHash } from "node:crypto";
import { DynamoIssueLedger, requestDigest } from "./ledger.ts";

const ddb = mockClient(DynamoDBDocumentClient);
let ledger: DynamoIssueLedger;
beforeEach(() => {
  ddb.reset();
  ledger = new DynamoIssueLedger("issue-requests", new DynamoDBClient({ region: "us-east-1" }));
});

const NOW = "2026-10-31T01:00:00.000Z";
const cancelled = (...codes: string[]) =>
  new TransactionCanceledException({ message: "cancelled", $metadata: {}, CancellationReasons: codes.map((Code) => ({ Code })) });

describe("DynamoIssueLedger（#2216 の I48・I49）", () => {
  it("依頼の行は無いときだけの Put で作り、あれば強い整合で今の行を読む", async () => {
    ddb.on(PutCommand).resolves({});
    expect(await ledger.claim("sr-1", "d1", "job-1:1", NOW)).toEqual({ created: true });
    expect(ddb.commandCalls(PutCommand)[0]!.args[0].input).toMatchObject({
      Item: { pk: "RUN#sr-1", sk: "ISSUE#d1", state: "filing", request_key: "job-1:1" },
      ConditionExpression: "attribute_not_exists(pk)",
    });

    ddb.on(PutCommand).rejects(new ConditionalCheckFailedException({ message: "x", $metadata: {} }));
    ddb.on(GetCommand).resolves({ Item: { state: "filed", request_key: "job-1:1", issue_repo: "org-b/repo-b", issue_number: 3, issue_url: "u" } });
    expect(await ledger.claim("sr-1", "d1", "job-2:1", NOW)).toEqual({
      created: false,
      row: { state: "filed", requestKey: "job-1:1", issue: { repo: "org-b/repo-b", number: 3, url: "u" } },
    });
    expect(ddb.commandCalls(GetCommand)[0]!.args[0].input.ConsistentRead).toBe(true);
  });

  it("数の増加と attempted_at は 1 回のトランザクションで、数の行の条件で取り消されたときだけ上限と読む", async () => {
    ddb.on(TransactWriteCommand).resolves({});
    expect(await ledger.reserve("sr-1", "d1", 10, NOW)).toBe(true);
    const items = ddb.commandCalls(TransactWriteCommand)[0]!.args[0].input.TransactItems!;
    expect(items.map((i) => i.Update!.Key)).toEqual([{ pk: "RUN#sr-1", sk: "FILED_COUNT" }, { pk: "RUN#sr-1", sk: "ISSUE#d1" }]);
    expect(items[0]!.Update!.ExpressionAttributeValues).toMatchObject({ ":limit": 10 });

    ddb.on(TransactWriteCommand).rejects(cancelled("ConditionalCheckFailed", "None"));
    expect(await ledger.reserve("sr-1", "d1", 10, NOW)).toBe(false);
    ddb.on(TransactWriteCommand).rejects(cancelled("None", "ConditionalCheckFailed"));
    await expect(ledger.reserve("sr-1", "d1", 10, NOW)).rejects.toThrow();
  });

  it("要約値は鍵の順にそろえた JSON の sha256 で、起票先の大文字と小文字を問わない", () => {
    const expected = createHash("sha256").update('{"body":"本文","repo":"org-b/repo-b","title":"題"}').digest("hex");
    expect(requestDigest("Org-B/Repo-B", "題", "本文")).toBe(expected);
  });
});
