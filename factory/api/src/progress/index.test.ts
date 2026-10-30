import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mockClient } from "aws-sdk-client-mock";
import { DynamoDBDocumentClient, TransactWriteCommand } from "@aws-sdk/lib-dynamodb";
import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";
import * as entry from "../index.ts";

const env = {
  RUN_JOB_TOKEN_VERIFY_KEYS_SECRET_ARN: "arn:aws:secretsmanager:us-east-1:123456789012:secret:verify-keys",
  PROGRESS_EVENTS_TABLE_NAME: "events",
  PROGRESS_IDEMPOTENCY_TABLE_NAME: "keys",
  AWS_REGION: "us-east-1",
};

beforeEach(() => {
  Object.assign(process.env, env);
});
afterEach(() => {
  for (const name of Object.keys(env)) delete process.env[name];
});

describe("公開の入口（受け入れ条件 19・20）", () => {
  it("index.ts の発行・検証・テストの鍵の組で往復でき、verifyRunJobToken は秘密の公開鍵の集合で確かめる", async () => {
    const kit = await entry.createRunJobTokenKit();
    const sm = mockClient(SecretsManagerClient);
    sm.on(GetSecretValueCommand).resolves({ SecretString: JSON.stringify(kit.publicJwks) });
    const token = await kit.sign({ jobId: "job-1", sprintRunId: "sr-1" });
    expect(await entry.verifyRunJobToken(token)).toMatchObject({ jobId: "job-1", sprintRunId: "sr-1" });
    expect(await entry.createRunJobTokenVerifier(kit.publicJwks.keys)(token)).toMatchObject({ jobId: "job-1" });
    await expect(entry.verifyRunJobToken("x")).rejects.toBeInstanceOf(entry.RunJobTokenError);
    sm.restore();
  });

  it("appendControlEvent は環境変数の表へ 1 回のトランザクションで書く", async () => {
    const ddb = mockClient(DynamoDBDocumentClient);
    ddb.on(TransactWriteCommand).resolves({});
    const out = await entry.appendControlEvent({
      sprint_run_id: "sr-1",
      idempotency_key: "job-1:finished",
      type: "job.finished",
      at: "2026-10-30T02:00:00Z",
      source: "control",
      job_id: "job-1",
      schema_version: 1,
      payload: { result: "failed", summary: "止まった" },
    });
    expect(out).toEqual({ written: true });
    expect(ddb.commandCalls(TransactWriteCommand)[0]!.args[0].input.TransactItems!.map((i) => i.Put!.TableName)).toEqual(["keys", "events"]);
    await expect(entry.appendControlEvent({ type: "job.started" } as never)).rejects.toBeInstanceOf(entry.ProgressEventError);
    ddb.restore();
  });

  it("既存の export（#2170）は残る", () => {
    for (const name of ["createApiApp", "verifyRequest", "resolveAccount", "setPlan", "getLimits", "judgeLimits", "AccountService", "ApiError"]) {
      expect(entry).toHaveProperty(name);
    }
    expect(typeof entry.createIngestApp).toBe("function");
    expect(typeof entry.signRunJobToken).toBe("function");
  });
});
