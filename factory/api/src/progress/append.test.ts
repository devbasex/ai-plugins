import { beforeEach, describe, expect, it } from "vitest";
import type { IssueRequestResolvedEvent, JobFinishedEvent } from "@ndf-factory/contracts";
import { appendControlEventTo, appendIssueRequestResolvedTo, orderKey } from "./append.ts";
import { ProgressEventError } from "./errors.ts";
import { MemoryProgressEventStore } from "./memory-store.ts";
import { isCallerRole } from "./authorize.ts";
import { verifyWebhookSignature } from "./github.ts";

let store: MemoryProgressEventStore;
beforeEach(() => {
  store = new MemoryProgressEventStore();
});

const finished = (patch: Partial<JobFinishedEvent> = {}): JobFinishedEvent => ({
  sprint_run_id: "sr-1",
  idempotency_key: "job-1:finished",
  type: "job.finished",
  at: "2026-10-30T02:00:00Z",
  source: "control",
  job_id: "job-1",
  schema_version: 1,
  payload: { result: "failed", summary: "ECS が止めた" },
  ...patch,
});

async function errorOf(p: Promise<unknown>): Promise<string> {
  try {
    await p;
  } catch (err) {
    expect(err).toBeInstanceOf(ProgressEventError);
    return (err as ProgressEventError).code;
  }
  throw new Error("拒まれなかった");
}

describe("appendControlEvent（受け入れ条件 20、決定 9）", () => {
  it("ランナーの job.finished が無ければ { written: true } で 1 件残る", async () => {
    expect(await appendControlEventTo(store, finished())).toEqual({ written: true });
    expect(store.events).toHaveLength(1);
  });

  it("ランナーの job.finished が先にあれば { written: false } で書かれない", async () => {
    await store.append({ ...finished({ source: "runner", payload: { result: "done", summary: "済んだ" } }), order_key: "x" });
    expect(await appendControlEventTo(store, finished())).toEqual({ written: false });
    expect(store.events).toHaveLength(1);
    expect(store.events[0]!.source).toBe("runner");
  });

  it("job.finished 以外・鍵が <job_id>:finished でない・source が control でないものは forbidden", async () => {
    const started = { ...finished(), type: "job.started", payload: { ndf_version: "1", start: "new" } } as unknown as JobFinishedEvent;
    expect(await errorOf(appendControlEventTo(store, started))).toBe("forbidden");
    expect(await errorOf(appendControlEventTo(store, finished({ idempotency_key: "job-1:3" })))).toBe("forbidden");
    expect(await errorOf(appendControlEventTo(store, finished({ idempotency_key: "job-2:finished" })))).toBe("forbidden");
    expect(await errorOf(appendControlEventTo(store, finished({ source: "runner" })))).toBe("forbidden");
    expect(store.events).toHaveLength(0);
  });

  it("定義に合わないものは invalid_event", async () => {
    expect(await errorOf(appendControlEventTo(store, finished({ payload: { result: "lost" as "done", summary: "" } })))).toBe("invalid_event");
    expect(store.events).toHaveLength(0);
  });
});

const resolved = (patch: Partial<IssueRequestResolvedEvent> = {}): IssueRequestResolvedEvent => ({
  sprint_run_id: "sr-1",
  idempotency_key: "job-1:4:resolved",
  type: "issue.request_resolved",
  at: "2026-10-30T02:00:00Z",
  source: "control",
  job_id: "job-1",
  schema_version: 1,
  payload: { request_key: "job-1:4", outcome: "drafted", reason: "not_registered", duplicate_of: null },
  ...patch,
});

describe("appendIssueRequestResolved（#2216 の決定 10、I48）", () => {
  it("依頼 1 件に結果は 1 件だけ残る", async () => {
    expect(await appendIssueRequestResolvedTo(store, resolved())).toEqual({ written: true });
    expect(await appendIssueRequestResolvedTo(store, resolved({ at: "2026-10-30T03:00:00Z" }))).toEqual({ written: false });
    expect(store.events).toHaveLength(1);
  });

  it("鍵が <依頼の鍵>:resolved でない・依頼が別のジョブ・source が control でない・ほかの種類は forbidden", async () => {
    expect(await errorOf(appendIssueRequestResolvedTo(store, resolved({ idempotency_key: "job-1:5:resolved" })))).toBe("forbidden");
    expect(await errorOf(appendIssueRequestResolvedTo(store, resolved({ job_id: "job-2", idempotency_key: "job-1:4:resolved" })))).toBe("forbidden");
    expect(await errorOf(appendIssueRequestResolvedTo(store, resolved({ source: "runner" })))).toBe("forbidden");
    expect(await errorOf(appendIssueRequestResolvedTo(store, finished() as unknown as IssueRequestResolvedEvent))).toBe("forbidden");
    expect(store.events).toHaveLength(0);
  });

  it("appendControlEvent は issue.request_resolved を受けない", async () => {
    expect(await errorOf(appendControlEventTo(store, resolved() as unknown as JobFinishedEvent))).toBe("forbidden");
  });
});

describe("並びの鍵（決定 6）", () => {
  it("小数の桁が違う時刻をミリ秒の 3 桁にそろえ、文字列の順が時刻の順になる", () => {
    const keys = ["2026-10-30T00:00:00.5Z", "2026-10-30T00:00:00Z", "2026-10-30T00:00:00.123456Z"].map((at) =>
      orderKey({ ...finished(), at }),
    );
    expect(keys).toEqual([
      "2026-10-30T00:00:00.500Z#job-1:finished",
      "2026-10-30T00:00:00.000Z#job-1:finished",
      "2026-10-30T00:00:00.123Z#job-1:finished",
    ]);
    expect([...keys].sort()).toEqual([keys[1], keys[2], keys[0]]);
  });
});

describe("呼び出し元のロールの照合（決定 12）", () => {
  const role = "arn:aws:iam::123456789012:role/service/approval-api";
  it.each([
    ["arn:aws:sts::123456789012:assumed-role/approval-api/session-a", true],
    ["arn:aws:sts::123456789012:assumed-role/approval-api/session-b", true],
    ["arn:aws:sts::123456789012:assumed-role/approval-api-2/s", false],
    ["arn:aws:sts::210987654321:assumed-role/approval-api/s", false],
    ["arn:aws-cn:sts::123456789012:assumed-role/approval-api/s", false],
    ["arn:aws:iam::123456789012:user/approval-api", false],
    ["approval-api", false],
  ])("%s → %s", (caller, expected) => {
    expect(isCallerRole(caller, role)).toBe(expected);
  });

  it("パスの無いロールの ARN とも照らせ、ロールの ARN でない設定とは一致しない", () => {
    expect(isCallerRole("arn:aws:sts::123456789012:assumed-role/r/s", "arn:aws:iam::123456789012:role/r")).toBe(true);
    expect(isCallerRole("arn:aws:sts::123456789012:assumed-role/r/s", "arn:aws:iam::123456789012:user/r")).toBe(false);
  });
});

describe("Webhook の署名", () => {
  it("長さが違う署名は比べずに false", () => {
    expect(verifyWebhookSignature("s", "{}", "sha256=abc")).toBe(false);
    expect(verifyWebhookSignature("s", "{}", undefined)).toBe(false);
  });
});
