// 進捗の受け口の app（決定 4・11）。経路が認証の手段を 1 つに決める
//   POST /ingest/events      runner  Authorization: Bearer <実行ジョブの token>
//   POST /ingest/api/events  api     API Gateway の IAM 認可と、呼び出し元のロールの照合
//   POST /ingest/github      github  X-Hub-Signature-256
import { Hono, type Context } from "hono";
import type { ProgressEvent } from "@ndf-factory/contracts";
import { ApiError } from "../errors.ts";
import { appendEvent } from "./append.ts";
import { authorizeApi, authorizeGithub, authorizeRunner, isCallerRole } from "./authorize.ts";
import { IngestForbiddenError, IngestUnauthorizedError, IngestUnavailableError, InvalidEventError } from "./errors.ts";
import { translateWebhook, verifyWebhookSignature } from "./github.ts";
import { RunJobTokenError, type RunJobTokenVerifier } from "./run-job-token.ts";
import type { SavedAnswerReader } from "./saved-answers.ts";
import type { ProgressEventStore } from "./store.ts";
import { checkBuiltEvent, checkEvent, parseEventBody } from "./validate.ts";

export interface IngestDeps {
  verifier: RunJobTokenVerifier;
  store: ProgressEventStore;
  savedAnswers: SavedAnswerReader;
  // GitHub App の Webhook の秘密。読めない失敗は 503
  webhookSecret: () => Promise<string>;
  // api の出所として認める #2173 のサービスのロールの ARN
  apiCallerRoleArn: string;
}

// ログは 1 要求 1 行で、この 4 列だけを出す。token・秘密・本文は出さない（I31）
export interface IngestLogEntry {
  route: string;
  type: string | null;
  status: number;
  sprint_run_id: string | null;
}

export type IngestLogger = (entry: IngestLogEntry) => void;

export const jsonIngestLogger: IngestLogger = (entry) => console.log(JSON.stringify(entry));

// API Gateway の HTTP API（payload 2.0）の IAM 認可の文脈。hono/aws-lambda は c.env.event に元の事象を置く
interface LambdaEnv {
  event?: { requestContext?: { authorizer?: { iam?: { userArn?: unknown } } } };
}

type IngestEnv = {
  Bindings: LambdaEnv;
  Variables: { eventType: string | null; sprintRunId: string | null };
};

const ACCEPTED = { accepted: true } as const;

function bearerToken(header: string | undefined): string | null {
  const m = /^Bearer ([^\s]+)$/.exec(header ?? "");
  return m ? m[1]! : null;
}

export function createIngestApp(
  deps: IngestDeps | (() => IngestDeps),
  log: IngestLogger = jsonIngestLogger,
): Hono<IngestEnv> {
  const resolve = typeof deps === "function" ? deps : () => deps;
  const app = new Hono<IngestEnv>();

  app.use("*", async (c, next) => {
    c.set("eventType", null);
    c.set("sprintRunId", null);
    await next();
    log({ route: c.req.path, type: c.get("eventType"), status: c.res.status, sprint_run_id: c.get("sprintRunId") });
  });

  app.onError((err, c) => {
    const known = err instanceof ApiError ? err : new IngestUnavailableError();
    return c.json(known.body(), known.status);
  });

  // 本文の大きさ・JSON（400）を見てから、定義（400）で確かめる。ログの列もここで決める
  async function readEvent<T>(
    c: Context<IngestEnv>,
    authenticate: () => Promise<T>,
  ): Promise<{ event: ProgressEvent; auth: T }> {
    const parsed = parseEventBody(await c.req.text());
    if (!parsed.ok) throw new InvalidEventError(parsed.message);
    const auth = await authenticate();
    const checked = checkEvent(parsed.value);
    if (!checked.ok) throw new InvalidEventError(checked.message);
    c.set("eventType", checked.event.type);
    c.set("sprintRunId", checked.event.sprint_run_id);
    return { event: checked.event, auth };
  }

  async function append(event: ProgressEvent): Promise<void> {
    try {
      await appendEvent(resolve().store, event);
    } catch {
      throw new IngestUnavailableError();
    }
  }

  app.post("/ingest/events", async (c) => {
    const { event, auth: job } = await readEvent(c, async () => {
      const token = bearerToken(c.req.header("Authorization"));
      if (!token) throw new IngestUnauthorizedError();
      try {
        return await resolve().verifier(token);
      } catch (err) {
        if (err instanceof RunJobTokenError) throw new IngestUnauthorizedError();
        throw new IngestUnavailableError();
      }
    });
    if (!authorizeRunner(event, job)) throw new IngestForbiddenError();
    await append(event);
    return c.json(ACCEPTED, 202);
  });

  app.post("/ingest/api/events", async (c) => {
    const { event } = await readEvent(c, async () => {
      const callerArn = c.env?.event?.requestContext?.authorizer?.iam?.userArn;
      if (typeof callerArn !== "string" || callerArn.length === 0) throw new IngestUnauthorizedError();
      if (!isCallerRole(callerArn, resolve().apiCallerRoleArn)) throw new IngestForbiddenError();
    });
    let allowed: boolean;
    try {
      allowed = await authorizeApi(event, resolve().savedAnswers);
    } catch {
      throw new IngestUnavailableError();
    }
    if (!allowed) throw new IngestForbiddenError();
    await append(event);
    return c.json(ACCEPTED, 202);
  });

  // GitHub は 2xx 以外を配達の失敗として記録し続けるため、署名が合った後は 400・403 を返さず 202 で保存しない（前提 7）
  app.post("/ingest/github", async (c) => {
    const body = await c.req.text();
    let secret: string;
    try {
      secret = await resolve().webhookSecret();
    } catch {
      throw new IngestUnavailableError();
    }
    if (!verifyWebhookSignature(secret, body, c.req.header("X-Hub-Signature-256"))) throw new IngestUnauthorizedError();

    const eventName = c.req.header("X-GitHub-Event");
    const delivery = c.req.header("X-GitHub-Delivery");
    if (!eventName || !delivery) return c.json(ACCEPTED, 202);
    let parsed: unknown;
    try {
      parsed = JSON.parse(body);
    } catch {
      return c.json(ACCEPTED, 202);
    }
    const translated = translateWebhook(eventName, delivery, parsed);
    if (!translated) return c.json(ACCEPTED, 202);

    // 宛先は Pull Request の参照を持つ事象のスプリントの実行。ちょうど 1 つに決まるときだけ残す（決定 7）
    const targets = new Map<string, number>();
    try {
      for (const candidate of translated.candidates) {
        for (const run of await resolve().store.findSprintRunsByPrRef(candidate.prRef)) {
          if (!targets.has(run)) targets.set(run, candidate.number);
        }
      }
    } catch {
      throw new IngestUnavailableError();
    }
    if (targets.size !== 1) return c.json(ACCEPTED, 202);
    const [[sprintRunId, number]] = [...targets] as [[string, number]];

    const checked = checkBuiltEvent(translated.build(sprintRunId, number));
    if (!checked.ok || !authorizeGithub(checked.event)) return c.json(ACCEPTED, 202);
    c.set("eventType", checked.event.type);
    c.set("sprintRunId", checked.event.sprint_run_id);
    await append(checked.event);
    return c.json(ACCEPTED, 202);
  });

  return app;
}
