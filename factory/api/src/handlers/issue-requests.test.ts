import { marshall } from "@aws-sdk/util-dynamodb";
import type { DynamoDBStreamEvent } from "aws-lambda";
import { describe, expect, it } from "vitest";
import type { IssueRequestDeps } from "../issue-requests/resolve.ts";
import { handleIssueRequests, issueRequestLimit, requestFromRecord } from "./issue-requests.ts";

const item = (type: string) => ({
  sprint_run_id: "sr-1",
  idempotency_key: "job-1:1",
  type,
  at: "2026-10-31T00:30:00.000Z",
  source: "runner",
  job_id: "job-1",
  schema_version: 1,
  payload: { repo: "org-b/repo-b", title: "題", body: "本文" },
  order_key: "2026-10-31T00:30:00.000Z#job-1:1",
});

const record = (type: string, eventName: "INSERT" | "MODIFY" = "INSERT", seq = "1") =>
  ({ eventName, dynamodb: { SequenceNumber: seq, NewImage: marshall(item(type)) } }) as unknown as DynamoDBStreamEvent["Records"][number];

describe("起票の代行の handler（#2216）", () => {
  it("新しい issue.requested だけを取り出す", () => {
    expect(requestFromRecord(record("issue.requested"))).toMatchObject({ type: "issue.requested", payload: { repo: "org-b/repo-b" } });
    expect(requestFromRecord(record("step.recorded"))).toBeNull();
    expect(requestFromRecord(record("issue.requested", "MODIFY"))).toBeNull();
  });

  it("投げた依頼だけを失敗として返す", async () => {
    const deps = { ledger: { claim: async () => Promise.reject(new Error("表を読めない")) } } as unknown as IssueRequestDeps;
    const out = await handleIssueRequests({ Records: [record("step.recorded", "INSERT", "1"), record("issue.requested", "INSERT", "2")] }, deps);
    expect(out).toEqual({ batchItemFailures: [{ itemIdentifier: "2" }] });
  });

  it("上限は ISSUE_REQUEST_LIMIT_PER_RUN で変えられ、無い・形が違えば 10", () => {
    expect(issueRequestLimit({ ISSUE_REQUEST_LIMIT_PER_RUN: "3" })).toBe(3);
    expect(issueRequestLimit({})).toBe(10);
    expect(issueRequestLimit({ ISSUE_REQUEST_LIMIT_PER_RUN: "0" })).toBe(10);
    expect(issueRequestLimit({ ISSUE_REQUEST_LIMIT_PER_RUN: "abc" })).toBe(10);
  });
});
