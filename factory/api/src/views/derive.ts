// スプリントの一覧・詳細・工程の中身・棚卸しの記録の派生（#2172）。進捗の事象の列とスプリントの実行の行から作る純粋な関数で、
// 時刻も外の世界も読まず、どちらの集約も書き換えない。リンクは GitHub を呼ばずに文字列で作る（I13）
import { CLOSING_STEPS, PHASE_NAMES } from "@ndf-factory/contracts/phases";
import type { ApprovalRequestRow, AttentionRow, StepDetailBody } from "@ndf-factory/contracts/views";
import type { AttentionPayload, CiUpdatedPayload, GateAnsweredPayload, GateReachedPayload, PrUpdatedPayload, StepRecordedPayload, UsageRecordedPayload } from "@ndf-factory/contracts";
import type { AwaitingGate, RunLatest, StepRecord } from "../progress/read.ts";
import type { ProgressEventRecord } from "../progress/store.ts";
import type { SprintRun } from "../runs/model.ts";

// 詳細で読む step.recorded の上限（過去の 11 スプリントの最大 233 件の約 8.6 倍。決定 9）
export const DETAIL_STEP_LIMIT = 2000;

const GITHUB = "https://github.com";

export interface PullRequestLink {
  repo: string;
  number: number;
  url: string;
}

export interface StepRow {
  key: string;
  at: string;
  job_id: string | null;
  step: string;
  // payload.phase、無ければ同じ実行ジョブの直前の phase.changed の phase。どちらも無ければ null（I7）
  phase: string | null;
  issues: number[];
  exit_code: number;
  seconds: number;
  // 同じ job_id・step・at の usage.recorded の cost_usd の和。無ければ null（I8）
  cost_usd: number | null;
  summary: string;
  pull_request: PullRequestLink | null;
  // 工程の中身の本文（触ったファイル・コミットの範囲・決めたこと・出力）を持つか
  has_details: boolean;
}

export interface RequiredStep {
  label: string;
  step: string;
  record: { key: string; exit_code: number; seconds: number } | null;
}

export interface PhaseView {
  name: string;
  step_keys: string[];
  required: RequiredStep[];
}

export interface PullRequestView extends PullRequestLink {
  state: "open" | "closed" | "merged" | "unknown";
  checks: { name: string; conclusion: string | null }[];
}

export interface IssueColumn {
  // null はスプリント全体の欄
  issue: number | null;
  step_keys: string[];
  pull_requests: PullRequestView[];
}

export type GateState = "not_reached" | "waiting" | "approved" | "rejected";
export type DecisionState = "none" | "waiting" | "answered";

export interface GatesView {
  gate1: { state: GateState; at: string | null };
  gate2: { state: GateState; at: string | null };
  decision: { state: DecisionState; at: string | null };
}

export interface SprintRunSummaryView {
  sprint_run_id: string;
  repository_id: number;
  issues: number[];
  state: string;
  failure_reason: string | null;
  created_at: string;
}

export interface SprintRunDetail extends SprintRunSummaryView {
  repo: string;
  ndf_version: string;
  execution_mode: string;
  job_count: number;
  current_phase: string | null;
  phases: PhaseView[];
  columns: IssueColumn[];
  steps: StepRow[];
  gates: GatesView;
  delegated_decisions: number;
  usage: { input_tokens: number; output_tokens: number; cost_usd: number };
  omitted_steps: number;
  attention: AttentionRow[];
}

export type StepDetail = StepRow & StepDetailBody;

export interface RefinementRecord {
  sprint_run_id: string;
  key: string;
  at: string;
  exit_code: number;
  summary: string;
  pull_request: PullRequestLink | null;
}

type Rec = ProgressEventRecord & { body_fields?: string[] };

const round6 = (n: number) => Math.round(n * 1e6) / 1e6;
const prKey = (repo: string, number: number) => `${repo.toLowerCase()}#${number}`;
const prLink = (ref: { repo: string; number: number } | undefined): PullRequestLink | null =>
  ref ? { repo: ref.repo, number: ref.number, url: `${GITHUB}/${ref.repo}/pull/${ref.number}` } : null;

const isStep = (e: Rec): e is StepRecord & Rec => e.type === "step.recorded";
const BODY_FIELDS = ["files", "commit_range", "decisions", "output"] as const;
const payloadOf = <T>(e: ProgressEventRecord) => e.payload as unknown as T;

export function summaryOf(run: SprintRun): SprintRunSummaryView {
  return {
    sprint_run_id: run.sprintRunId,
    repository_id: run.repositoryId,
    issues: run.issues,
    state: run.state,
    failure_reason: run.failureReason,
    created_at: run.createdAt,
  };
}

// 一覧の行に足す 4 項目（I4）。phase.changed が 0 件なら current_phase は null（画面は「始まる前」）
export function listRowExtras(run: Pick<SprintRun, "repo">, latest: RunLatest) {
  return {
    repo: run.repo,
    current_phase: latest.phase ? payloadOf<{ phase: string }>(latest.phase).phase : null,
    current_step: latest.step ? latest.step.payload.step : null,
    last_event_at: latest.last ? latest.last.at : null,
  };
}

// 工程の記録の行（order_key の順）。events は order_key の順の列
export function stepRows(events: Rec[]): StepRow[] {
  const cost = new Map<string, number>();
  for (const e of events) {
    if (e.type !== "usage.recorded") continue;
    const u = payloadOf<UsageRecordedPayload>(e);
    const k = `${e.job_id}\n${u.step}\n${e.at}`;
    cost.set(k, (cost.get(k) ?? 0) + u.cost_usd);
  }
  const phaseOfJob = new Map<string | null, string>();
  const rows: StepRow[] = [];
  for (const e of events) {
    if (e.type === "phase.changed") phaseOfJob.set(e.job_id, payloadOf<{ phase: string }>(e).phase);
    if (!isStep(e)) continue;
    const p: StepRecordedPayload = e.payload;
    const c = cost.get(`${e.job_id}\n${p.step}\n${e.at}`);
    rows.push({
      key: e.idempotency_key,
      at: e.at,
      job_id: e.job_id,
      step: p.step,
      phase: p.phase ?? phaseOfJob.get(e.job_id) ?? null,
      issues: p.issues ?? [],
      exit_code: p.exit_code,
      seconds: p.seconds,
      cost_usd: c === undefined ? null : round6(c),
      summary: p.summary,
      pull_request: prLink(p.pull_request),
      // 読み出しの口は本文を外して持っていた項目の名前を body_fields に残す。外す前の列（テスト）は本文そのものを見る
      has_details: e.body_fields !== undefined || BODY_FIELDS.some((k) => p[k] !== undefined),
    });
  }
  return rows;
}

// 5 つの名前の順に 5 つ、その後に現れた順に 5 つに無い名前。仕上げは振り返りと棚卸しの行を常に持つ（I10）
function phasesOf(rows: StepRow[], events: Rec[]): PhaseView[] {
  const others: string[] = [];
  const seen = (name: string | null) => {
    if (name !== null && !(PHASE_NAMES as readonly string[]).includes(name) && !others.includes(name)) others.push(name);
  };
  for (const e of events) if (e.type === "phase.changed") seen(payloadOf<{ phase: string }>(e).phase);
  for (const r of rows) seen(r.phase);
  return [...PHASE_NAMES, ...others].map((name) => ({
    name,
    step_keys: rows.filter((r) => r.phase === name).map((r) => r.key),
    required:
      name === "仕上げ"
        ? CLOSING_STEPS.map(({ label, step }) => {
            const last = rows.filter((r) => r.step === step).at(-1);
            return { label, step, record: last ? { key: last.key, exit_code: last.exit_code, seconds: last.seconds } : null };
          })
        : [],
  }));
}

// PR ごとの最新の状態と、チェックの名前ごとの最新の結論。どちらも後の更新が勝つ
function pullRequestIndexes(events: Rec[]): {
  prState: Map<string, PullRequestView["state"]>;
  checks: Map<string, Map<string, string | null>>;
} {
  const prState = new Map<string, PullRequestView["state"]>();
  const checks = new Map<string, Map<string, string | null>>();
  for (const e of events) {
    if (e.type === "pr.updated") {
      const p = payloadOf<PrUpdatedPayload>(e);
      prState.set(prKey(p.repo, p.number), p.state);
    }
    if (e.type === "ci.updated") {
      const p = payloadOf<CiUpdatedPayload>(e);
      const k = prKey(p.repo, p.pr_number);
      if (!checks.has(k)) checks.set(k, new Map());
      checks.get(k)!.set(p.name, p.conclusion);
    }
  }
  return { prState, checks };
}

// 課題の欄。実行の課題の順に、続いて実行に無い課題を現れた順に、最後にスプリント全体の欄（I6）
function columnsOf(run: SprintRun, rows: StepRow[], events: Rec[]): IssueColumn[] {
  const issues = [...run.issues];
  for (const r of rows) for (const n of r.issues) if (!issues.includes(n)) issues.push(n);
  const { prState, checks } = pullRequestIndexes(events);
  const column = (issue: number | null): IssueColumn => {
    const own = rows.filter((r) => (issue === null ? r.issues.length === 0 : r.issues.includes(issue)));
    const prs = new Map<string, PullRequestView>();
    for (const r of own) {
      if (!r.pull_request) continue;
      const k = prKey(r.pull_request.repo, r.pull_request.number);
      if (prs.has(k)) continue;
      prs.set(k, {
        ...r.pull_request,
        state: prState.get(k) ?? "unknown",
        checks: [...(checks.get(k) ?? new Map<string, string | null>()).entries()].map(([name, conclusion]) => ({ name, conclusion })),
      });
    }
    return { issue, step_keys: own.map((r) => r.key), pull_requests: [...prs.values()] };
  };
  return [...issues.map(column), column(null)];
}

// 承認の欄（I9）。その種類の最後の gate.reached と、それより後の同じ種類の gate.answered から決める
export function gatesOf(events: Rec[]): GatesView {
  const gates: GatesView = {
    gate1: { state: "not_reached", at: null },
    gate2: { state: "not_reached", at: null },
    decision: { state: "none", at: null },
  };
  for (const e of events) {
    if (e.type === "gate.reached") {
      const { kind } = payloadOf<GateReachedPayload>(e);
      gates[kind] = { state: "waiting", at: e.at };
    }
    if (e.type === "gate.answered") {
      const p = payloadOf<GateAnsweredPayload>(e);
      if (gates[p.kind].state !== "waiting") continue;
      if (p.kind === "decision") gates.decision = { state: "answered", at: e.at };
      else gates[p.kind] = { state: p.decision === "rejected" ? "rejected" : "approved", at: e.at };
    }
  }
  return gates;
}

// 注意の行（#2430 の I1・I2）。attention を events の順（order_key の順）にすべて。理由と要約は受けた値のまま
export function attentionsOf(events: Rec[]): AttentionRow[] {
  return events
    .filter((e) => e.type === "attention")
    .map((e) => {
      const p = payloadOf<AttentionPayload>(e);
      return { key: e.idempotency_key, at: e.at, reason: p.reason, summary: p.summary };
    });
}

// 承認待ちの一覧の行。待っている依頼が無ければ null
export function approvalRequestOf(run: SprintRun, gate: AwaitingGate | null): ApprovalRequestRow | null {
  if (!gate) return null;
  const loop = gate.payload.loop;
  return {
    sprint_run_id: run.sprintRunId,
    repository_id: run.repositoryId,
    repo: run.repo,
    issues: run.issues,
    kind: gate.payload.kind,
    loop: loop ? { name: loop.name, lap: loop.lap } : null,
    waiting_since: gate.at,
  };
}

// 待ち始めた日時の古い順、同じ日時は実行の ID の順（I5）
export function sortApprovalRequests(rows: ApprovalRequestRow[]): ApprovalRequestRow[] {
  const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
  return [...rows].sort((a, b) => cmp(a.waiting_since, b.waiting_since) || cmp(a.sprint_run_id, b.sprint_run_id));
}

function usageOf(events: Rec[]) {
  let input = 0;
  let output = 0;
  let cost = 0;
  for (const e of events) {
    if (e.type !== "usage.recorded") continue;
    const u = payloadOf<UsageRecordedPayload>(e);
    input += u.input_tokens;
    output += u.output_tokens;
    cost += u.cost_usd;
  }
  return { input_tokens: input, output_tokens: output, cost_usd: round6(cost) };
}

// スプリントの詳細。events は order_key の順で、本文を外したもの
export function deriveDetail(run: SprintRun, events: Rec[], omittedSteps: number): SprintRunDetail {
  const rows = stepRows(events);
  const lastPhase = events.filter((e) => e.type === "phase.changed").at(-1);
  return {
    ...summaryOf(run),
    repo: run.repo,
    ndf_version: run.ndfVersion,
    execution_mode: run.executionMode,
    job_count: run.jobCount,
    current_phase: lastPhase ? payloadOf<{ phase: string }>(lastPhase).phase : null,
    phases: phasesOf(rows, events),
    columns: columnsOf(run, rows, events),
    steps: rows,
    gates: gatesOf(events),
    delegated_decisions: events.filter((e) => e.type === "decision.delegated").length,
    usage: usageOf(events),
    omitted_steps: omittedSteps,
    attention: attentionsOf(events),
  };
}

const blobUrl = (repo: string, sha: string, path: string) => `${GITHUB}/${repo}/blob/${sha}/${path.split("/").map(encodeURIComponent).join("/")}`;

// 工程の中身。row は同じ工程の行（events から導いたもの）、record は本文を含む step.recorded
export function deriveStepDetail(run: Pick<SprintRun, "repo">, row: StepRow, record: StepRecord): StepDetail {
  const p = record.payload;
  const range = p.commit_range ?? null;
  const diff = row.pull_request ? `${row.pull_request.url}/files` : range ? `${GITHUB}/${run.repo}/compare/${range.from}...${range.to}` : null;
  return {
    ...row,
    files: p.files ? p.files.map((f) => ({ ...f, url: range ? blobUrl(run.repo, range.to, f.path) : null })) : null,
    files_omitted: p.files_omitted ?? null,
    commit_range: range,
    decisions: p.decisions ?? null,
    output: p.output ?? null,
    diff_url: diff,
  };
}

// そのリポジトリの実行の棚卸しの工程（step が refine）を at の新しい順に
export function deriveRefinements(perRun: { sprintRunId: string; steps: StepRecord[] }[]): RefinementRecord[] {
  return perRun
    .flatMap(({ sprintRunId, steps }) =>
      steps.map((e) => ({
        sprint_run_id: sprintRunId,
        key: e.idempotency_key,
        at: e.at,
        exit_code: e.payload.exit_code,
        summary: e.payload.summary,
        pull_request: prLink(e.payload.pull_request),
      })),
    )
    .sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
}
