// 起票の依頼の台帳（#2216 の「データ構造」、I48・I49）。スプリントの実行ごとに、依頼の中身の要約値の行と起票した数の行を持つ
import { createHash } from "node:crypto";
import { ConditionalCheckFailedException, DynamoDBClient, TransactionCanceledException } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, GetCommand, PutCommand, TransactWriteCommand, UpdateCommand } from "@aws-sdk/lib-dynamodb";
import type { CreatedIssue } from "../github/port.ts";
import { repoKey } from "../progress/github.ts";

export type DraftReason =
  | "not_registered"
  | "not_installed"
  | "unsupported_tracker"
  | "visibility"
  | "secret_suspected"
  | "limit_exceeded"
  | "filing_failed";

export type Settled = { state: "filed"; issue: CreatedIssue } | { state: "drafted"; reason: DraftReason };

export type LedgerRow =
  | { state: "filing"; requestKey: string; attemptedAt: string | null }
  | ({ requestKey: string } & Settled);

export type Claim = { created: true } | { created: false; row: LedgerRow };

export interface IssueLedger {
  // 行が無ければ filing で書く。あれば書かずに今の行を返す
  claim(sprintRunId: string, digest: string, requestKey: string, now: string): Promise<Claim>;
  // 起票した数を limit より少ないときだけ 1 増やし、同じトランザクションで依頼の行に attempted_at を書く。数が上限なら false
  reserve(sprintRunId: string, digest: string, limit: number, now: string): Promise<boolean>;
  // reserve で増やした数を 1 つ戻す（Issue を作れなかった）
  release(sprintRunId: string): Promise<void>;
  settle(sprintRunId: string, digest: string, outcome: Settled, now: string): Promise<void>;
}

// 起票先・題・本文の要約値。NDF の issue-file.py の提示の要約値と同じ作り方（鍵の順にそろえた JSON の sha256）で、ラベルを含めない
export function requestDigest(repo: string, title: string, body: string): string {
  return createHash("sha256").update(JSON.stringify({ body, repo: repoKey(repo), title }), "utf8").digest("hex");
}

const pk = (sprintRunId: string) => `RUN#${sprintRunId}`;
const requestSk = (digest: string) => `ISSUE#${digest}`;
const COUNT_SK = "FILED_COUNT";

type Item = Record<string, unknown>;

function toRow(item: Item): LedgerRow {
  const requestKey = item.request_key as string;
  if (item.state === "filed") {
    return { state: "filed", requestKey, issue: { repo: item.issue_repo as string, number: item.issue_number as number, url: item.issue_url as string } };
  }
  if (item.state === "drafted") return { state: "drafted", requestKey, reason: item.reason as DraftReason };
  return { state: "filing", requestKey, attemptedAt: (item.attempted_at as string | undefined) ?? null };
}

export class DynamoIssueLedger implements IssueLedger {
  private readonly doc: DynamoDBDocumentClient;

  constructor(
    private readonly table: string,
    client: DynamoDBClient = new DynamoDBClient({}),
  ) {
    this.doc = DynamoDBDocumentClient.from(client, { marshallOptions: { removeUndefinedValues: true } });
  }

  async claim(sprintRunId: string, digest: string, requestKey: string, now: string): Promise<Claim> {
    const key = { pk: pk(sprintRunId), sk: requestSk(digest) };
    try {
      await this.doc.send(
        new PutCommand({
          TableName: this.table,
          Item: { ...key, state: "filing", request_key: requestKey, updated_at: now },
          ConditionExpression: "attribute_not_exists(pk)",
        }),
      );
      return { created: true };
    } catch (err) {
      if (!(err instanceof ConditionalCheckFailedException)) throw err;
    }
    const out = await this.doc.send(new GetCommand({ TableName: this.table, Key: key, ConsistentRead: true }));
    if (!out.Item) throw new Error("台帳の行を読めません");
    return { created: false, row: toRow(out.Item) };
  }

  async reserve(sprintRunId: string, digest: string, limit: number, now: string): Promise<boolean> {
    try {
      await this.doc.send(
        new TransactWriteCommand({
          TransactItems: [
            {
              Update: {
                TableName: this.table,
                Key: { pk: pk(sprintRunId), sk: COUNT_SK },
                UpdateExpression: "SET #c = if_not_exists(#c, :zero) + :one",
                ConditionExpression: "attribute_not_exists(#c) OR #c < :limit",
                ExpressionAttributeNames: { "#c": "count" },
                ExpressionAttributeValues: { ":zero": 0, ":one": 1, ":limit": limit },
              },
            },
            {
              Update: {
                TableName: this.table,
                Key: { pk: pk(sprintRunId), sk: requestSk(digest) },
                UpdateExpression: "SET attempted_at = :now, updated_at = :now",
                ConditionExpression: "#s = :filing",
                ExpressionAttributeNames: { "#s": "state" },
                ExpressionAttributeValues: { ":now": now, ":filing": "filing" },
              },
            },
          ],
        }),
      );
      return true;
    } catch (err) {
      // 数の行の条件で取り消された分だけを上限として読む。ほかは投げてやり直させる
      if (err instanceof TransactionCanceledException && err.CancellationReasons?.[0]?.Code === "ConditionalCheckFailed") return false;
      throw err;
    }
  }

  async release(sprintRunId: string): Promise<void> {
    await this.doc.send(
      new UpdateCommand({
        TableName: this.table,
        Key: { pk: pk(sprintRunId), sk: COUNT_SK },
        UpdateExpression: "SET #c = #c - :one",
        ConditionExpression: "#c > :zero",
        ExpressionAttributeNames: { "#c": "count" },
        ExpressionAttributeValues: { ":zero": 0, ":one": 1 },
      }),
    ).catch((err: unknown) => {
      if (!(err instanceof ConditionalCheckFailedException)) throw err;
    });
  }

  async settle(sprintRunId: string, digest: string, outcome: Settled, now: string): Promise<void> {
    const values: Item =
      outcome.state === "filed"
        ? { ":s": "filed", ":r": outcome.issue.repo, ":n": outcome.issue.number, ":u": outcome.issue.url, ":now": now }
        : { ":s": "drafted", ":reason": outcome.reason, ":now": now };
    const update =
      outcome.state === "filed"
        ? "SET #s = :s, issue_repo = :r, issue_number = :n, issue_url = :u, updated_at = :now"
        : "SET #s = :s, reason = :reason, updated_at = :now";
    await this.doc.send(
      new UpdateCommand({
        TableName: this.table,
        Key: { pk: pk(sprintRunId), sk: requestSk(digest) },
        UpdateExpression: update,
        ExpressionAttributeNames: { "#s": "state" },
        ExpressionAttributeValues: values,
      }),
    );
  }
}
