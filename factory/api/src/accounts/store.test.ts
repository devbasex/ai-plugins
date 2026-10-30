import { beforeEach, describe, expect, it } from "vitest";
import { mockClient } from "aws-sdk-client-mock";
import { DynamoDBClient, TransactionCanceledException } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, GetCommand, QueryCommand, TransactWriteCommand } from "@aws-sdk/lib-dynamodb";
import { ConditionFailedError, DynamoAccountStore } from "./store.ts";
import type { AccountRecord, SeatRecord } from "./model.ts";

const ddb = mockClient(DynamoDBDocumentClient);
let store: DynamoAccountStore;
beforeEach(() => {
  ddb.reset();
  store = new DynamoAccountStore("accounts", new DynamoDBClient({ region: "us-east-1" }));
});

const account: AccountRecord = {
  accountId: "a-1",
  kind: "personal",
  plan: "free",
  executionMode: "local",
  version: 1,
  ownerUserId: "u-1",
  name: null,
  createdAt: "2026-10-30T00:00:00.000Z",
  updatedAt: "2026-10-30T00:00:00.000Z",
};

const seat: SeatRecord = { accountId: "t-1", userId: "u-2", seatType: "standard", addedAt: "2026-10-30T00:00:00.000Z" };

describe("DynamoAccountStore（決定 8）", () => {
  it("利用者と個人のアカウントを、無いときだけの条件と履歴つきで 1 つのトランザクションで書く", async () => {
    ddb.on(TransactWriteCommand).resolves({});
    await store.createUserWithPersonalAccount(
      { userId: "u-1", idp: "google", personalAccountId: "a-1", createdAt: account.createdAt },
      account,
      [{ accountId: "a-1", type: "account.created", at: account.createdAt, actor: "u-1", before: null, after: { plan: "free" } }],
    );
    const items = ddb.commandCalls(TransactWriteCommand)[0]!.args[0].input.TransactItems!;
    expect(items.map((i) => [i.Put?.Item?.pk, i.Put?.Item?.sk, i.Put?.ConditionExpression])).toEqual([
      ["USER#u-1", "USER", "attribute_not_exists(pk)"],
      ["ACCOUNT#a-1", "ACCOUNT", "attribute_not_exists(pk)"],
      ["ACCOUNT#a-1", expect.stringMatching(/^HIST#2026-10-30T00:00:00.000Z#/), undefined],
    ]);
    expect(items[1]!.Put!.Item).toMatchObject({ execution_mode: "local", version: 1, plan: "free" });
  });

  it("席の行は GSI の鍵を持つ", async () => {
    ddb.on(TransactWriteCommand).resolves({});
    await store.addSeat({ accountId: "t-1", userId: "u-2", seatType: "premium", addedAt: account.createdAt }, []);
    const put = ddb.commandCalls(TransactWriteCommand)[0]!.args[0].input.TransactItems![0]!.Put!;
    expect(put.Item).toMatchObject({ pk: "ACCOUNT#t-1", sk: "SEAT#u-2", gsi1pk: "USER#u-2", gsi1sk: "ACCOUNT#t-1", seat_type: "premium" });
    expect(put.ConditionExpression).toBe("attribute_not_exists(pk)");
  });

  it("アカウントの更新は版を条件にし、版を 1 増やす", async () => {
    ddb.on(TransactWriteCommand).resolves({});
    await store.updateAccount("a-1", 4, { plan: "free", executionMode: "local" }, account.updatedAt, []);
    const update = ddb.commandCalls(TransactWriteCommand)[0]!.args[0].input.TransactItems![0]!.Update!;
    expect(update.ConditionExpression).toBe("attribute_exists(#pk) AND #version = :expected");
    expect(update.ExpressionAttributeValues).toMatchObject({ ":expected": 4, ":next": 5, ":plan": "free", ":mode": "local" });
  });

  it("条件の失敗は ConditionFailedError に、ほかの失敗はそのまま投げる", async () => {
    ddb.on(TransactWriteCommand).rejectsOnce(
      new TransactionCanceledException({
        message: "cancelled",
        $metadata: {},
        CancellationReasons: [{ Code: "ConditionalCheckFailed" }, { Code: "None" }],
      }),
    );
    await expect(store.removeSeat(seat, [])).rejects.toBeInstanceOf(ConditionFailedError);
    ddb.on(TransactWriteCommand).rejects(new Error("throttled"));
    await expect(store.removeSeat(seat, [])).rejects.toThrow("throttled");
  });

  it("席の変更と削除は、読んだ席の種類と足した時刻を条件にする", async () => {
    ddb.on(TransactWriteCommand).resolves({});
    await store.updateSeat(seat, "premium", []);
    await store.removeSeat(seat, []);
    const [update, remove] = ddb.commandCalls(TransactWriteCommand).map((c) => c.args[0].input.TransactItems![0]!);
    const condition = "attribute_exists(#pk) AND #seat_type = :expected_type AND #added_at = :expected_added_at";
    expect(update!.Update).toMatchObject({
      ConditionExpression: condition,
      ExpressionAttributeValues: { ":t": "premium", ":expected_type": "standard", ":expected_added_at": seat.addedAt },
    });
    expect(remove!.Delete).toMatchObject({
      ConditionExpression: condition,
      ExpressionAttributeValues: { ":expected_type": "standard", ":expected_added_at": seat.addedAt },
    });
  });

  it("読み出しは強い整合で、行を値へ戻す", async () => {
    ddb.on(GetCommand).resolves({
      Item: { pk: "ACCOUNT#a-1", sk: "ACCOUNT", account_id: "a-1", kind: "personal", plan: "pro", execution_mode: "managed", version: 3, owner_user_id: "u-1", name: null, created_at: "x", updated_at: "y" },
    });
    expect(await store.getAccount("a-1")).toMatchObject({ plan: "pro", executionMode: "managed", version: 3 });
    expect(ddb.commandCalls(GetCommand)[0]!.args[0].input).toMatchObject({ Key: { pk: "ACCOUNT#a-1", sk: "ACCOUNT" }, ConsistentRead: true });
  });

  it("席を持つチームは GSI を頁をたどって引く", async () => {
    const seat = (a: string) => ({ account_id: a, user_id: "u-2", seat_type: "standard", added_at: "x" });
    ddb.on(QueryCommand).resolvesOnce({ Items: [seat("t-1")], LastEvaluatedKey: { pk: "k" } }).resolvesOnce({ Items: [seat("t-2")] });
    expect((await store.listMemberships("u-2")).map((s) => s.accountId)).toEqual(["t-1", "t-2"]);
    expect(ddb.commandCalls(QueryCommand)[0]!.args[0].input).toMatchObject({ IndexName: "gsi1", ExpressionAttributeValues: { ":u": "USER#u-2" } });
  });
});
