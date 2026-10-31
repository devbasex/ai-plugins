// 起票の代行の Lambda の handler（#2216）。事象の表の DynamoDB Streams のうち、type が issue.requested の新しい項目を
// 1 件ずつ resolveIssueRequest へ渡す。投げた分だけを失敗として返し、ストリームがやり直す（ストリームとフィルタはインフラが持つ）
import type { AttributeValue } from "@aws-sdk/client-dynamodb";
import { unmarshall } from "@aws-sdk/util-dynamodb";
import type { DynamoDBBatchResponse, DynamoDBStreamEvent } from "aws-lambda";
import type { IssueRequestedEvent } from "@ndf-factory/contracts";
import { DynamoIssueLedger } from "../issue-requests/ledger.ts";
import { DEFAULT_ISSUE_REQUEST_LIMIT_PER_RUN, resolveIssueRequest, type IssueRequestDeps } from "../issue-requests/resolve.ts";
import { defaultFilingStyle } from "../issue-requests/style.ts";
import { appendIssueRequestResolvedTo } from "../progress/append.ts";
import { factoryDepsFromEnv } from "../runs/deps.ts";
import { progressStoreFromEnv } from "./ingest.ts";

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`環境変数 ${name} がありません`);
  return value;
}

// 上限は ISSUE_REQUEST_LIMIT_PER_RUN（1 以上の整数）。無い・形が違えば既定の 10
export function issueRequestLimit(env: Record<string, string | undefined>): number {
  const n = Number(env.ISSUE_REQUEST_LIMIT_PER_RUN);
  return Number.isInteger(n) && n >= 1 ? n : DEFAULT_ISSUE_REQUEST_LIMIT_PER_RUN;
}

let deps: IssueRequestDeps | null = null;

function depsFromEnv(): IssueRequestDeps {
  if (deps) return deps;
  const factory = factoryDepsFromEnv();
  const store = progressStoreFromEnv();
  deps = {
    getSprintRun: (id) => factory.table.getRun(id),
    listRegistrations: (accountId) => factory.table.listRegistrations(accountId),
    github: factory.github,
    style: defaultFilingStyle,
    ledger: new DynamoIssueLedger(requireEnv("ISSUE_REQUESTS_TABLE_NAME")),
    append: (e) => appendIssueRequestResolvedTo(store, e),
    limitPerRun: issueRequestLimit(process.env),
    now: () => new Date().toISOString(),
    // 本文・題・token は載せない（決定 12）
    log: (entry) => console.log(JSON.stringify(entry)),
  };
  return deps;
}

// ストリームの項目から issue.requested を取り出す。ほかの種類・INSERT でない項目は null
export function requestFromRecord(record: DynamoDBStreamEvent["Records"][number]): IssueRequestedEvent | null {
  const image = record.dynamodb?.NewImage;
  if (record.eventName !== "INSERT" || !image) return null;
  const item = unmarshall(image as Record<string, AttributeValue>);
  return item.type === "issue.requested" ? (item as unknown as IssueRequestedEvent) : null;
}

export async function handleIssueRequests(event: DynamoDBStreamEvent, d: IssueRequestDeps): Promise<DynamoDBBatchResponse> {
  const batchItemFailures: DynamoDBBatchResponse["batchItemFailures"] = [];
  for (const record of event.Records) {
    const request = requestFromRecord(record);
    if (!request) continue;
    try {
      await resolveIssueRequest(request, d);
    } catch {
      // 例外の文は本文を含みうるため出さない。鍵だけを残してやり直させる
      console.error(JSON.stringify({ type: "issue.requested", request_key: request.idempotency_key, retry: true }));
      batchItemFailures.push({ itemIdentifier: record.dynamodb?.SequenceNumber ?? "" });
    }
  }
  return { batchItemFailures };
}

export const handler = (event: DynamoDBStreamEvent) => handleIssueRequests(event, depsFromEnv());
