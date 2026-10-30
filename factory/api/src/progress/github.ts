// GitHub の Webhook の翻訳（腐敗防止層）。署名を確かめ、pull_request・check_run を pr.updated・ci.updated へ移す。
// GitHub の語（delivery・check suite など）は事象へ持ち込まない
import { createHmac, timingSafeEqual } from "node:crypto";
import type { CiUpdatedEvent, PrUpdatedEvent } from "@ndf-factory/contracts";

// X-Hub-Signature-256（sha256=<16 進>）を Webhook の秘密による本文の HMAC-SHA256 と比べる
export function verifyWebhookSignature(secret: string, body: string, header: string | undefined | null): boolean {
  if (!header?.startsWith("sha256=")) return false;
  const given = Buffer.from(header.slice("sha256=".length), "utf8");
  const expected = Buffer.from(createHmac("sha256", secret).update(body, "utf8").digest("hex"), "utf8");
  if (given.length !== expected.length) return false;
  return timingSafeEqual(given, expected);
}

// Pull Request の参照。owner/name を小文字にそろえる（GitHub のリポジトリの名前は大文字と小文字を区別しない）
export const prRef = (repo: string, number: number) => `${repo.toLowerCase()}#${number}`;

// 宛先のスプリントの実行がまだ決まっていない事象と、宛先を引く参照の候補
export interface TranslatedWebhook {
  candidates: { prRef: string; number: number }[];
  build(sprintRunId: string, number: number): PrUpdatedEvent | CiUpdatedEvent;
}

type Json = Record<string, unknown>;
const obj = (v: unknown): Json | null => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Json) : null);
const str = (v: unknown): string | null => (typeof v === "string" && v.length > 0 ? v : null);
const int = (v: unknown): number | null => (Number.isInteger(v) && (v as number) >= 1 ? (v as number) : null);

// 扱わない種類・形の違う本文・時刻の無い本文は null（202 で保存しない）
export function translateWebhook(eventName: string, delivery: string, body: unknown): TranslatedWebhook | null {
  const root = obj(body);
  const repo = str(obj(root?.repository)?.full_name);
  if (!root || !repo) return null;
  const common = {
    idempotency_key: `github:${delivery}`,
    source: "github" as const,
    job_id: null,
    schema_version: 1 as const,
  };

  if (eventName === "pull_request") {
    const pr = obj(root.pull_request);
    const number = int(pr?.number);
    const at = str(pr?.updated_at);
    const state = pr?.merged === true ? "merged" : pr?.state;
    if (!pr || !number || !at || (state !== "open" && state !== "closed" && state !== "merged")) return null;
    return {
      candidates: [{ prRef: prRef(repo, number), number }],
      build: (sprintRunId) => ({ ...common, sprint_run_id: sprintRunId, type: "pr.updated", at, payload: { repo, number, state } }),
    };
  }

  if (eventName === "check_run") {
    const run = obj(root.check_run);
    const name = str(run?.name);
    const at = str(run?.completed_at) ?? str(run?.started_at);
    const conclusion = run?.conclusion === null || run?.conclusion === undefined ? null : str(run.conclusion);
    const numbers = Array.isArray(run?.pull_requests)
      ? run.pull_requests.map((p) => int(obj(p)?.number)).filter((n): n is number => n !== null)
      : [];
    if (!run || !name || !at || numbers.length === 0) return null;
    return {
      candidates: [...new Set(numbers)].map((number) => ({ prRef: prRef(repo, number), number })),
      build: (sprintRunId, number) => ({
        ...common,
        sprint_run_id: sprintRunId,
        type: "ci.updated",
        at,
        payload: { repo, pr_number: number, name, conclusion },
      }),
    };
  }

  return null;
}
