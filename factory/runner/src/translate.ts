// 翻訳（腐敗防止層、決定 6）。NDF の書くファイル（sprint.json・progress.jsonl・作業の報告・claude の stream-json）を
// 進捗の事象へ移す。NDF の語はここから外へ持ち出さない
import type {
  AttentionEvent,
  DecisionDelegatedEvent,
  GateKind,
  GateReachedEvent,
  IssueRequestedEvent,
  JobFinishedEvent,
  PhaseChangedEvent,
  ProgressEvent,
  StepRecordedEvent,
  UsageRecordedEvent,
} from "@ndf-factory/contracts";
import type { PhaseName } from "@ndf-factory/contracts/phases";
import { normalizePhase } from "./legacy-phases.ts";

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

// プランの JSON の "フェーズ" の値からフェーズの名前への写し（#2172 の決定 1）。まとめ（pace: fast / auto の close）は振り返りと棚卸しを持つため仕上げ
const PLAN_PHASES: Readonly<Record<string, PhaseName>> = {
  設計: "設計",
  実装: "実装",
  検査: "検査",
  開発版のデプロイ: "取り込み",
  リリース: "仕上げ",
  まとめ: "仕上げ",
};

// フェーズの名前。プランの JSON が読めない（null）・表に無い値は、計画の名前の断片をそのまま返す（画面は「その他」と出す）
export function phaseNameOf(planName: string, planPhase: string | null): string {
  const phase = planPhase === null ? null : normalizePhase(planPhase);
  if (phase !== null && Object.hasOwn(PLAN_PHASES, phase)) return PLAN_PHASES[phase]!;
  return phaseOf(planName).phase;
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

// 工程の中身（#2172）。どれも任意で、集められなかった項目は持たない
export type StepDetails = Pick<StepRecordedEvent["payload"], "phase" | "issues" | "files" | "files_omitted" | "commit_range" | "decisions" | "output">;

// progress.jsonl の 1 行から事象の本体（common を除く）を作る。扱わない行は null。
// step の行には工程の中身を添え、事象 1 件の上限に収める
export function translateProgressLine(
  line: Line,
  repo: string,
  details: StepDetails = {},
): Pick<StepRecordedEvent, "type" | "payload"> | Pick<AttentionEvent, "type" | "payload"> | null {
  if (line.kind === "step" && typeof line.step === "string") {
    const summary = summaryOf(line.summary);
    const payload: StepRecordedEvent["payload"] = {
      step: line.step,
      exit_code: Number.isInteger(line.exit) ? (line.exit as number) : 0,
      seconds: typeof line.seconds === "number" && line.seconds >= 0 ? line.seconds : 0,
      summary,
    };
    // supervise は pr のステップの URL を step 行の pr へ書く（summary は末尾の要約で切れる）
    const pr = findPullRequest([line.pr, line.summary, line.report].map((v) => String(v ?? "")).join("\n"), repo);
    if (pr) payload.pull_request = pr;
    Object.assign(payload, Object.fromEntries(Object.entries(details).filter(([, v]) => v !== undefined)));
    return { type: "step.recorded", payload: fitStep(payload) };
  }
  if (line.kind === "attention") {
    // 取り込み（base-sync）で解けずに止まった行は Factory の語 base_conflict へ写す（#2347 の決定 10）。ほかの行の理由は変えない
    const reason =
      line.step === "base-sync" ? "base_conflict" : typeof line.reason === "string" && line.reason !== "" ? line.reason : "attention";
    const summary = typeof line.text === "string" ? line.text : typeof line.summary === "string" ? line.summary : "";
    return { type: "attention", payload: { reason, summary } };
  }
  return null;
}

// planPhase はプランの JSON の "フェーズ" の値（読めなければ null）
export function phaseChanged(planName: string, planPhase: string | null = null): Pick<PhaseChangedEvent, "type" | "payload"> {
  return { type: "phase.changed", payload: { phase: phaseNameOf(planName, planPhase), issues: phaseOf(planName).issues } };
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

// 承認の依頼の種類。計画の名前が設計なら gate1、リリースなら gate2、ほかは判断
export function gateKindOf(planName: string | null): GateKind {
  if (!planName) return "decision";
  if (/design/.test(planName)) return "gate1";
  if (/release-prod|release/.test(planName)) return "gate2";
  return "decision";
}

// 種類ごとの NDF の承認ゲートの記録の名前と、人へ出す名前。ランナーが入れる NDF の版は利用者が選ぶため（前の版は新しい名前を読めない）、
// 記録には旧名を渡し続ける。新しい版の NDF は旧名を受けて正の名前で書く。旧名と正の名前の対応はここにだけ置く
const GATE_NAMES: Readonly<Record<string, { record: string; display: string }>> = {
  gate1: { record: "関門 1", display: "設計承認ゲート" },
  gate2: { record: "関門 2", display: "リリース承認ゲート" },
};

// 承認の依頼の種類から、NDF のスプリント状態に書く承認ゲートの名前（sprint-state.py gate の名）
export function gateRecordName(kind: string): string {
  return GATE_NAMES[kind]?.record ?? kind;
}

// 記録の名前から人へ出す名前を作る。旧名でない名前はそのまま返す
export function gateDisplayName(recordName: string): string {
  return Object.values(GATE_NAMES).find((g) => g.record === recordName)?.display ?? recordName;
}

// 差し戻しの答えから、再開する conductor への指示を作る（承認と区別できるように差し戻しを明示する）
export function rejectionDirective(displayName: string, body: string | null | undefined): string {
  const reason = body && body.trim() !== "" ? body.trim() : "理由の記載なし";
  return `利用者は${displayName}を差し戻した（理由: ${reason}）。承認ゲートを通さず、差し戻された工程をこの理由に沿って直してから、もう一度${displayName}の承認を求めてください。`;
}

// スプリント状態ファイル（sprint-state.py init が作る）か。目録の sprint.json（supervise.py new。「ステージ」を持つ）とは別
export function isSprintState(json: unknown): boolean {
  if (typeof json !== "object" || json === null || Array.isArray(json)) return false;
  const o = json as Record<string, unknown>;
  return Array.isArray(o.gates) && Array.isArray(o.plans) && !("ステージ" in o);
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

// 共通の列（common）に見込む大きさ。step.recorded の本体はこれを除いた分に収める
const COMMON_BUDGET = 1024;

// UTF-8 で max バイト以下になるよう、文字の境目で切る
export function cutUtf8(text: string, max: number): { text: string; cut: boolean } {
  if (Buffer.byteLength(text, "utf8") <= max) return { text, cut: false };
  let out = Buffer.from(text, "utf8").subarray(0, max).toString("utf8");
  // 途中で切れた文字は U+FFFD になるため落とす
  while (out.endsWith("\uFFFD") || Buffer.byteLength(out, "utf8") > max) out = out.slice(0, -1);
  return { text: out, cut: true };
}

// 事象 1 件の上限（256 KiB）を超える step.recorded を output.text → files → decisions の順に削る（I2）。
// 削っても超えるなら工程の中身をすべて外す（足す前の形に戻る）
export function fitStep(payload: StepRecordedEvent["payload"]): StepRecordedEvent["payload"] {
  const limit = MAX_EVENT_BYTES - COMMON_BUDGET;
  const size = (p: StepRecordedEvent["payload"]) => bytes({ type: "step.recorded", payload: p });
  if (size(payload) <= limit) return payload;
  let p: StepRecordedEvent["payload"] = { ...payload };
  if (p.output) {
    const over = size(p) - limit;
    const keep = Math.max(0, Buffer.byteLength(p.output.text, "utf8") - over);
    p = { ...p, output: { ...p.output, text: cutUtf8(p.output.text, keep).text, truncated: true } };
  }
  while (size(p) > limit && p.files && p.files.length > 0) {
    const drop = Math.max(1, Math.ceil(p.files.length / 2));
    p = { ...p, files: p.files.slice(0, p.files.length - drop), files_omitted: (p.files_omitted ?? 0) + drop };
  }
  while (size(p) > limit && p.decisions && p.decisions.length > 0) p = { ...p, decisions: p.decisions.slice(0, -1) };
  if (size(p) <= limit) return p;
  const { step, exit_code, seconds, summary, pull_request } = payload;
  return { step, exit_code, seconds, summary, ...(pull_request ? { pull_request } : {}) };
}

// #2206 の代わりの判断の記録（1 行 1 件）から decision.delegated を作る。形が違う行は送らない。
// category は ask_ledger.py の decision の行に無いため、ref が指す判定の行（kind: verdict）の source から埋める
export function delegatedFromRecord(record: Line, verdictSources: ReadonlyMap<string, string> = new Map()): Pick<DecisionDelegatedEvent, "type" | "payload"> | null {
  const text = (v: unknown) => (typeof v === "string" && v.length > 0 ? v.slice(0, 4000) : null);
  const question = text(record.question);
  const answer = text(record.answer);
  const reason = text(record.reason);
  if (!question || !answer || !reason) return null;
  const source = typeof record.ref === "string" ? verdictSources.get(record.ref) : undefined;
  const category = source || (typeof record.category === "string" ? record.category : null);
  return { type: "decision.delegated", payload: { question, answer, reason, category } };
}

// 起票の依頼の上限（契約の IssueRequestedPayload と同じ。題は GitHub の題の上限、本文は 4 バイトの文字でも 256 KiB に収まる長さ）
export const ISSUE_TITLE_MAX = 256;
export const ISSUE_BODY_MAX = 50_000;
const REPO_FULL_NAME = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;

// NDF の issue-file.py が NDF_ISSUE_REQUEST_FILE へ書いた 1 行（{"repo", "title", "body"}）から issue.requested を作る（#2216）。
// 値はそのまま写す。題か本文が上限を超える行は送らず attention（issue_request_too_large）にし、形の違う行は送らない
export function issueRequestFromLine(line: Line): Pick<IssueRequestedEvent, "type" | "payload"> | Pick<AttentionEvent, "type" | "payload"> | null {
  const { repo, title, body } = line;
  if (typeof repo !== "string" || !REPO_FULL_NAME.test(repo) || typeof title !== "string" || typeof body !== "string" || !title || !body) return null;
  if (Array.from(title).length > ISSUE_TITLE_MAX || Array.from(body).length > ISSUE_BODY_MAX) {
    return { type: "attention", payload: { reason: "issue_request_too_large", summary: `${repo} への起票の依頼が上限を超えたため送らなかった` } };
  }
  return { type: "issue.requested", payload: { repo, title, body } };
}

// progress.jsonl の step の行の models（supervise が別のプロセスで起動した claude のモデルごとの使用量）から、
// モデルごとに 1 件。数え方は usageFromResult と同じ（cache の読み書きを入力に足す）
// ask.jsonl の判定の行（kind: verdict）の id から source を引く表。source が空の行は入れない
export function verdictSourcesOf(rows: Line[]): Map<string, string> {
  const out = new Map<string, string>();
  for (const r of rows) if (r.kind === "verdict" && typeof r.id === "string" && typeof r.source === "string" && r.source) out.set(r.id, r.source);
  return out;
}

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
