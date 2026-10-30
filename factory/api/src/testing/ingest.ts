// 受け口のテストの道具。偽の保存・テストの鍵の token・偽の保存済みの答えで app を作る
import { createHmac } from "node:crypto";
import { createIngestApp, type IngestLogEntry } from "../progress/app.ts";
import { MemoryProgressEventStore } from "../progress/memory-store.ts";
import type { SavedAnswer } from "../progress/saved-answers.ts";
import { createRunJobTokenKit, type RunJobTokenKit } from "./run-job-tokens.ts";
import type { TestResponse } from "./app.ts";

export const WEBHOOK_SECRET = "test-webhook-secret-value";
export const API_ROLE_ARN = "arn:aws:iam::123456789012:role/service/approval-api";
export const API_CALLER_ARN = "arn:aws:sts::123456789012:assumed-role/approval-api/lambda-session-1";

export interface TestIngest {
  app: ReturnType<typeof createIngestApp>;
  store: MemoryProgressEventStore;
  tokens: RunJobTokenKit;
  answers: Map<string, SavedAnswer>;
  logs: IngestLogEntry[];
  // 次の保存済みの答えの読み出しを失敗させる
  failAnswers: { next: boolean };
  failSecret: { next: boolean };
  runner(body: unknown, opts?: { authorization?: string | null; raw?: string }): Promise<TestResponse>;
  api(body: unknown, opts?: { callerArn?: string | null; raw?: string }): Promise<TestResponse>;
  github(event: string, body: unknown, opts?: { delivery?: string | null; signature?: string | null; raw?: string }): Promise<TestResponse>;
}

export const sign = (body: string, secret = WEBHOOK_SECRET) =>
  `sha256=${createHmac("sha256", secret).update(body, "utf8").digest("hex")}`;

export async function createTestIngest(): Promise<TestIngest> {
  const store = new MemoryProgressEventStore();
  const tokens = await createRunJobTokenKit();
  const answers = new Map<string, SavedAnswer>();
  const logs: IngestLogEntry[] = [];
  const failAnswers = { next: false };
  const failSecret = { next: false };
  const app = createIngestApp(
    {
      verifier: tokens.verify,
      store,
      savedAnswers: async (id) => {
        if (failAnswers.next) {
          failAnswers.next = false;
          throw new Error("読めない");
        }
        return answers.get(id) ?? null;
      },
      webhookSecret: async () => {
        if (failSecret.next) {
          failSecret.next = false;
          throw new Error("読めない");
        }
        return WEBHOOK_SECRET;
      },
      apiCallerRoleArn: API_ROLE_ARN,
    },
    (e) => logs.push(e),
  );

  const post = (path: string, raw: string, headers: Record<string, string>, env?: unknown) =>
    app.request(path, { method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: raw }, env as never) as Promise<TestResponse>;

  return {
    app,
    store,
    tokens,
    answers,
    logs,
    failAnswers,
    failSecret,
    async runner(body, opts = {}) {
      const headers: Record<string, string> = {};
      if (opts.authorization === undefined) {
        const e = body as { job_id?: string; sprint_run_id?: string };
        headers.Authorization = `Bearer ${await tokens.sign({ jobId: e.job_id ?? "job-1", sprintRunId: e.sprint_run_id ?? "sr-1" })}`;
      } else if (opts.authorization !== null) {
        headers.Authorization = opts.authorization;
      }
      return post("/ingest/events", opts.raw ?? JSON.stringify(body), headers);
    },
    async api(body, opts = {}) {
      const callerArn = opts.callerArn === undefined ? API_CALLER_ARN : opts.callerArn;
      const env = callerArn === null ? {} : { event: { requestContext: { authorizer: { iam: { userArn: callerArn } } } } };
      return post("/ingest/api/events", opts.raw ?? JSON.stringify(body), {}, env);
    },
    async github(event, body, opts = {}) {
      const raw = opts.raw ?? JSON.stringify(body);
      const headers: Record<string, string> = { "X-GitHub-Event": event };
      const delivery = opts.delivery === undefined ? "d-1" : opts.delivery;
      if (delivery !== null) headers["X-GitHub-Delivery"] = delivery;
      const signature = opts.signature === undefined ? sign(raw) : opts.signature;
      if (signature !== null) headers["X-Hub-Signature-256"] = signature;
      return post("/ingest/github", raw, headers);
    },
  };
}
