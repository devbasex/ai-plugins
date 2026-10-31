// 翻訳（腐敗防止層、決定 6）。NDF の書くファイル（sprint.json・progress.jsonl・作業の報告・claude の stream-json）を
// 進捗の事象へ移す。NDF の語はここから外へ持ち出さない
import type {
  AttentionEvent,
  DecisionDelegatedEvent,
  GateKind,
  GateReachedEvent,
  JobFinishedEvent,
  PhaseChangedEvent,
  ProgressEvent,
  StepRecordedEvent,
  UsageRecordedEvent,
} from "@ndf-factory/contracts";

export interface JobIdentity {
  sprintRunId: string;
  jobId: string;
  repo: string;
}

type Common = Pick<ProgressEvent, "sprint_run_id" | "idempotency_key" | "at" | "source" | "job_id" | "schema_version">;

export function common(id: JobIdentity, seq: number | "finished", at: string): Common {
  return {
    sprint_run_id: id.sprintRunId,
    idempotency_key: `${id.jobId}:${seq}`,
    at,
    source: "runner",
    job_id: id.jobId,
    schema_version: 1,
  };
}

const PR_URL = /https:\/\/github\.com\/([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)\/pull\/(\d+)/g;

// 文の中の Pull Request の URL のうち、REPO のものだけを拾う。ほかのリポジトリの URL は付けない（I37）
export function findPullRequest(text: string, repo: string): { repo: string; number: number } | null {
  for (const m of text.matchAll(PR_URL)) {
    if (m[1]!.toLowerCase() === repo.toLowerCase()) return { repo: m[1]!, number: Number(m[2]) };
  }
  return null;
}

// 計画の名前（5-impl-2171）からフェーズと課題を取る
export function phaseOf(planName: string): { phase: string; issues: number[] } {
  const base = planName.replace(/\.json$/, "").split("/").pop()!;
  const parts = base.split("-");
  if (/^\d+$/.test(parts[0] ?? "")) parts.shift();
  const issues = parts.filter((p) => /^\d+$/.test(p)).map(Number);
  const phase = parts.filter((p) => !/^\d+$/.test(p)).join("-") || base;
  return { phase, issues };
}

// summary が JSON の文字列なら summary の値を使う
function summaryOf(raw: unknown): string {
  if (typeof raw !== "string") return "";
  try {
    const parsed = JSON.parse(raw) as { summary?: unknown };
    if (typeof parsed.summary === "string") return parsed.summary;
  } catch {
    // JSON でなければそのまま
  }
  return raw;
}

type Line = Record<string, unknown>;

// progress.jsonl の 1 行から事象の本体（common を除く）を作る。扱わない行は null
export function translateProgressLine(line: Line, repo: string): Pick<StepRecordedEvent, "type" | "payload"> | Pick<AttentionEvent, "type" | "payload"> | null {
  if (line.kind === "step" && typeof line.step === "string") {
    const summary = summaryOf(line.summary);
    const payload: StepRecordedEvent["payload"] = {
      step: line.step,
      exit_code: Number.isInteger(line.exit) ? (line.exit as number) : 0,
      seconds: typeof line.seconds === "number" && line.seconds >= 0 ? line.seconds : 0,
      summary,
    };
    const pr = findPullRequest(`${String(line.summary ?? "")}\n${String(line.report ?? "")}`, repo);
    if (pr) payload.pull_request = pr;
    return { type: "step.recorded", payload };
  }
  if (line.kind === "attention") {
    const reason = typeof line.reason === "string" && line.reason !== "" ? line.reason : "attention";
    const summary = typeof line.text === "string" ? line.text : typeof line.summary === "string" ? line.summary : "";
    return { type: "attention", payload: { reason, summary } };
  }
  return null;
}

export function phaseChanged(planName: string): Pick<PhaseChangedEvent, "type" | "payload"> {
  return { type: "phase.changed", payload: phaseOf(planName) };
}

// claude の stream-json の result の modelUsage から、モデルごとに 1 件（#2170 の数え方: cache の読み書きを入力に足す）
export function usageFromResult(result: Line, step: string): Pick<UsageRecordedEvent, "type" | "payload">[] {
  const usage = result.modelUsage;
  if (typeof usage !== "object" || usage === null) return [];
  return Object.entries(usage as Record<string, Record<string, number>>).map(([model, u]) => ({
    type: "usage.recorded" as const,
    payload: {
      model,
      input_tokens: (u.inputTokens ?? 0) + (u.cacheReadInputTokens ?? 0) + (u.cacheCreationInputTokens ?? 0),
      output_tokens: u.outputTokens ?? 0,
      cost_usd: Math.max(0, u.costUSD ?? 0),
      step,
    },
  }));
}

export type ReportResult = "完了" | "判断が要る" | "できなかった" | null;

// 作業の報告の「結果:」の行
export function reportResult(report: string): ReportResult {
  const m = /^- 結果:\s*(完了|判断が要る|できなかった)/m.exec(report);
  return (m?.[1] as ReportResult) ?? null;
}

// 承認の依頼の種類。計画の名前が設計なら gate1、本番の配布なら gate2、ほかは判断
export function gateKindOf(planName: string | null): GateKind {
  if (!planName) return "decision";
  if (/design/.test(planName)) return "gate1";
  if (/release-prod|release/.test(planName)) return "gate2";
  return "decision";
}

export function finishedResult(report: ReportResult, failed: boolean): JobFinishedEvent["payload"]["result"] {
  if (failed) return "failed";
  if (report === "判断が要る") return "gate";
  if (report === "完了") return "done";
  if (report === "できなかった") return "stopped";
  return "failed";
}

export const MAX_EVENT_BYTES = 262_144;

const bytes = (v: unknown) => Buffer.byteLength(JSON.stringify(v), "utf8");

// 256 KiB を超える gate.reached は material だけを、UTF-8 の文字の境目で切り詰め、案内を足す
export function truncateGate(event: GateReachedEvent): GateReachedEvent {
  if (bytes(event) <= MAX_EVENT_BYTES) return event;
  const original = Buffer.byteLength(event.payload.material, "utf8");
  const tail = event.payload.pr_url
    ? `\n\n（ここから先を切り詰めた。全文は ${event.payload.pr_url} にある）`
    : `\n\n（ここから先を切り詰めた。元の大きさ ${original} バイト）`;
  const chars = Array.from(event.payload.material);
  let lo = 0;
  let hi = chars.length;
  const build = (n: number): GateReachedEvent => ({ ...event, payload: { ...event.payload, material: chars.slice(0, n).join("") + tail } });
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (bytes(build(mid)) <= MAX_EVENT_BYTES) lo = mid;
    else hi = mid - 1;
  }
  return build(lo);
}

// #2206 の代わりの判断の記録（1 行 1 件）から decision.delegated を作る。形が違う行は送らない
export function delegatedFromRecord(record: Line): Pick<DecisionDelegatedEvent, "type" | "payload"> | null {
  const text = (v: unknown) => (typeof v === "string" && v.length > 0 ? v.slice(0, 4000) : null);
  const question = text(record.question);
  const answer = text(record.answer);
  const reason = text(record.reason);
  if (!question || !answer || !reason) return null;
  const category = typeof record.category === "string" ? record.category : null;
  return { type: "decision.delegated", payload: { question, answer, reason, category } };
}

// progress.jsonl の step の行の models（supervise が別のプロセスで起動した claude のモデルごとの使用量）から、
// モデルごとに 1 件。数え方は usageFromResult と同じ（cache の読み書きを入力に足す）
export function usageFromStepLine(line: Line): Pick<UsageRecordedEvent, "type" | "payload">[] {
  if (line.kind !== "step" || typeof line.step !== "string") return [];
  const models = line.models;
  if (typeof models !== "object" || models === null) return [];
  const n = (v: unknown) => (typeof v === "number" && v >= 0 ? v : 0);
  return Object.entries(models as Record<string, Record<string, unknown>>).map(([model, u]) => ({
    type: "usage.recorded" as const,
    payload: {
      model,
      input_tokens: n(u.input) + n(u.cache_read) + n(u.cache_write),
      output_tokens: n(u.output),
      cost_usd: n(u.cost),
      step: line.step as string,
    },
  }));
}
