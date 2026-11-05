// API Gateway の HTTP API（payload 2.0）から呼ばれる Lambda の handler
import { handle } from "hono/aws-lambda";
import { createApiApp, jsonLogger, webOriginFromEnv } from "../app.ts";
import { depsFromEnv } from "../auth/deps.ts";
import { factoryDepsFromEnv } from "../runs/deps.ts";

export const handler = handle(createApiApp(depsFromEnv, jsonLogger, webOriginFromEnv, factoryDepsFromEnv));
