// ランナー向けの API の Lambda の handler（経路 /run-jobs/*、API Gateway の認可は付けない）。
// INGEST_URL と同じ origin に置く（決定 8）
import { handle } from "hono/aws-lambda";
import { factoryDepsFromEnv } from "../runs/deps.ts";
import { createRunnerApiApp } from "../runs/runner-api.ts";
import { runJobTokenVerifierFromEnv } from "../progress/run-job-token.ts";

export const handler = handle(
  createRunnerApiApp(() => {
    const d = factoryDepsFromEnv();
    return { verifier: runJobTokenVerifierFromEnv(), control: d.control, table: d.table, github: d.github, platform: d.platform };
  }),
);
