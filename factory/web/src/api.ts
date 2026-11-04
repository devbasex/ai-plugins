import type { Auth } from "./auth.ts";
import type { StepDetailBody } from "@ndf-factory/contracts/views";

// API の応答の形（factory/contracts/openapi/accounts.yaml）
export type Plan = "free" | "pro" | "max" | "team";
export type SeatType = "standard" | "premium";
export type ExecutionMode = "local" | "byok" | "managed";

export interface AccountSummary {
  account_id: string;
  kind: "personal" | "team";
  name: string | null;
  plan: Plan;
  role: "owner" | "member";
  seat_type: SeatType | null;
  execution_mode: ExecutionMode;
}

export interface Seat {
  user_id: string;
  idp: "google" | "github" | null;
  seat_type: SeatType;
  added_at: string;
}

export interface AccountDetail extends AccountSummary {
  limits: { concurrent_sprint_runs: number; monthly_tokens: number | null } | null;
  seats: Seat[] | null;
}

export interface Me {
  user_id: string;
  idp: "google" | "github";
  personal_account_id: string;
  accounts: AccountSummary[];
}

export class ApiFailure extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> | null = null,
  ) {
    super(message);
  }
}

// #2186 の経路（factory/contracts/openapi/runs.yaml）
export interface RepositoryCandidate {
  repository_id: number;
  full_name: string;
  installation_id: number;
  registered: boolean;
}

export interface Repository {
  repository_id: number;
  full_name: string;
  installation_id: number;
  registered_at: string;
  active_sprint_runs?: number;
  // 同時の実行の数の宣言。無いか null なら料金プランの値だけで数える
  max_concurrent_runs?: number | null;
}

export interface ModelCredential {
  registered: boolean;
  kind: "anthropic_api_key" | "aws_role" | null;
  updated_at: string | null;
  external_id: string;
}

export type ModelCredentialInput =
  | { kind: "anthropic_api_key"; api_key: string }
  | { kind: "aws_role"; role_arn: string; region: string };

export interface IssueCandidate {
  number: number;
  title: string;
  selected: boolean;
  reason: string;
}

export interface IssueCandidates {
  source: "ranking" | "recent";
  milestone: string | null;
  candidates: IssueCandidate[];
  note: string | null;
}

export interface SprintRunSummary {
  sprint_run_id: string;
  repository_id: number;
  issues: number[];
  state: string;
  failure_reason: string | null;
  created_at: string;
}

// 一覧の行（#2172）。今のフェーズは最後の phase.changed の名前で、null は始まる前。5 つの名前に無い値もそのまま来る
export interface SprintRunRow extends SprintRunSummary {
  repo: string;
  current_phase: string | null;
  current_step: string | null;
  last_event_at: string | null;
}

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
  phase: string | null;
  issues: number[];
  exit_code: number;
  seconds: number;
  cost_usd: number | null;
  summary: string;
  pull_request: PullRequestLink | null;
  has_details: boolean;
}

export type GateState = "not_reached" | "waiting" | "approved" | "rejected";
export type DecisionState = "none" | "waiting" | "answered";

export interface SprintRunDetail extends SprintRunSummary {
  repo: string;
  ndf_version: string;
  execution_mode: ExecutionMode;
  job_count: number;
  current_phase: string | null;
  phases: { name: string; step_keys: string[]; required: { label: string; step: string; record: { key: string; exit_code: number; seconds: number } | null }[] }[];
  columns: {
    issue: number | null;
    step_keys: string[];
    pull_requests: (PullRequestLink & { state: "open" | "closed" | "merged" | "unknown"; checks: { name: string; conclusion: string | null }[] })[];
  }[];
  steps: StepRow[];
  gates: { gate1: { state: GateState; at: string | null }; gate2: { state: GateState; at: string | null }; decision: { state: DecisionState; at: string | null } };
  delegated_decisions: number;
  usage: { input_tokens: number; output_tokens: number; cost_usd: number };
  omitted_steps: number;
}

export type StepDetail = StepRow & StepDetailBody;

export interface Backlog {
  repository_id: number;
  repo: string;
  ranking: { milestone: string; rows: { rank: number; number: number; title: string }[] } | null;
  refinements: { sprint_run_id: string; key: string; at: string; exit_code: number; summary: string; pull_request: PullRequestLink | null }[];
}

// #2347 の開発ループ（factory/contracts/openapi/loops.yaml）
export type LoopState = "running" | "between_laps" | "awaiting_approval" | "stopped" | "cancelled";
export type LoopWaitReason = "slots" | "legacy_version" | "user_start";

export interface LoopBudget {
  laps?: number | null;
  usd?: number | null;
  hours?: number | null;
}

export interface LoopRow {
  loop_id: string;
  name: string;
  milestones: string[];
  state: LoopState;
  wait_reason: LoopWaitReason | null;
  laps_closed: number;
  cost_usd: number;
  budget: LoopBudget;
  budget_remaining: LoopBudget;
  last_run_at: string | null;
  stop_condition: string | null;
  stop_detail: string | null;
}

export interface LoopStartInput {
  name: string;
  milestones: string[];
  budget?: LoopBudget;
}

export type LoopDecision = "approved" | "rejected";

export interface LoopPending {
  number: number;
  reason: string;
  lap: number;
  answer: { decision: LoopDecision; pr: number | null } | null;
}

export interface LoopLap {
  lap: number;
  issues: number[];
  // NDF の loop.json の周回の結果の語（完了・失敗・人へ戻した）をそのまま持つ
  result: string;
  version: string | null;
  cost_usd: number;
  seconds: number;
}

export interface LoopRun {
  sprint_run_id: string;
  lap: number;
  state: string;
  failure_reason: string | null;
  finish_reason: "section_limit" | "usage_limit" | null;
  resume_at: string | null;
  cost_usd: number;
  sections: number;
}

export interface LoopDetail {
  loop: LoopRow;
  laps: LoopLap[];
  runs: LoopRun[];
  pending: LoopPending[];
}

export interface Api {
  me(): Promise<Me>;
  account(id: string): Promise<AccountDetail>;
  createTeam(name: string, seatType: SeatType): Promise<AccountDetail>;
  setExecutionMode(id: string, mode: ExecutionMode): Promise<AccountDetail>;
  addSeat(id: string, userId: string, seatType: SeatType): Promise<Seat>;
  changeSeat(id: string, userId: string, seatType: SeatType): Promise<Seat>;
  removeSeat(id: string, userId: string): Promise<void>;
  installUrl(id: string): Promise<{ url: string }>;
  authorizeUrl(id: string): Promise<{ url: string }>;
  repositoryCandidates(id: string, code: string, state: string): Promise<{ proof: string; candidates: RepositoryCandidate[] }>;
  repositories(id: string): Promise<{ repositories: Repository[] }>;
  registerRepository(id: string, proof: string, repositoryId: number): Promise<Repository>;
  removeRepository(id: string, repositoryId: number): Promise<void>;
  modelCredential(id: string): Promise<ModelCredential>;
  saveModelCredential(id: string, input: ModelCredentialInput): Promise<ModelCredential>;
  deleteModelCredential(id: string): Promise<void>;
  issueCandidates(id: string, repositoryId: number, exclude: number[]): Promise<IssueCandidates>;
  startSprintRun(id: string, repositoryId: number, issues: number[]): Promise<{ sprint_run_id: string; state: string }>;
  // repositoryId を省くとアカウントの新しい順の 50 件（画面は 1 回だけ呼んでリポジトリごとに分ける）
  sprintRuns(id: string, repositoryId?: number): Promise<{ sprint_runs: SprintRunRow[] }>;
  // 実行の ID の配列で受け、要素の配列を返す（1 件のときも要素 1 個の配列）。無い実行は API が他人の実行と同じ 403 を返し、そのまま投げる
  sprintRunDetails(id: string, sprintRunIds: string[]): Promise<SprintRunDetail[]>;
  // 工程の中身。工程の鍵（事象の idempotency_key）の配列で受ける
  stepDetails(id: string, sprintRunId: string, stepKeys: string[]): Promise<StepDetail[]>;
  // バックログ（順位の表と棚卸しの記録）。リポジトリの ID の配列で受ける
  backlogs(id: string, repositoryIds: number[]): Promise<Backlog[]>;
  cancelSprintRun(id: string, sprintRunId: string): Promise<{ state: string }>;
  // 同時の実行の数の宣言（持ち主だけ）。null は宣言を外す
  updateRepository(id: string, repositoryId: number, maxConcurrentRuns: number | null): Promise<Repository>;
  loops(id: string, repositoryId: number): Promise<{ loops: LoopRow[] }>;
  startLoop(id: string, repositoryId: number, input: LoopStartInput): Promise<{ loop_id: string; state: LoopState }>;
  // ループの ID の配列で受け、見つかった要素の配列を返す。見つからない ID の要素は返さない
  loopDetails(id: string, loopIds: string[]): Promise<LoopDetail[]>;
  updateLoopBudget(id: string, loopId: string, budget: LoopBudget): Promise<LoopRow>;
  cancelLoop(id: string, loopId: string): Promise<{ state: LoopState }>;
  requestNextLap(id: string, loopId: string): Promise<{ state: LoopState }>;
  answerLoop(id: string, loopId: string, issue: number, decision: LoopDecision, pr?: number): Promise<{ pending: LoopPending[] }>;
}

// 401 では token を消す。画面は ApiFailure の status を見てログインへ移る
export function createApi(apiUrl: string, auth: Auth, doFetch: typeof fetch = (...a) => fetch(...a)): Api {
  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const token = auth.getToken();
    if (!token) throw new ApiFailure(401, "unauthorized", "unauthorized");
    const res = await doFetch(`${apiUrl}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (res.status === 204) return undefined as T;
    const data = (await res.json().catch(() => null)) as { error?: { code: string; message: string; details?: Record<string, unknown> } } | null;
    if (!res.ok) {
      if (res.status === 401) auth.clearToken();
      throw new ApiFailure(res.status, data?.error?.code ?? "error", data?.error?.message ?? "", data?.error?.details ?? null);
    }
    return data as T;
  }

  const seg = encodeURIComponent;
  return {
    me: () => request("GET", "/me"),
    account: (id) => request("GET", `/accounts/${seg(id)}`),
    createTeam: (name, seat_type) => request("POST", "/teams", { name, seat_type }),
    setExecutionMode: (id, execution_mode) => request("PUT", `/accounts/${seg(id)}/execution-mode`, { execution_mode }),
    addSeat: (id, user_id, seat_type) => request("POST", `/accounts/${seg(id)}/seats`, { user_id, seat_type }),
    changeSeat: (id, userId, seat_type) => request("PATCH", `/accounts/${seg(id)}/seats/${seg(userId)}`, { seat_type }),
    removeSeat: (id, userId) => request("DELETE", `/accounts/${seg(id)}/seats/${seg(userId)}`),
    installUrl: (id) => request("GET", `/accounts/${seg(id)}/github/install-url`),
    authorizeUrl: (id) => request("GET", `/accounts/${seg(id)}/github/authorize-url`),
    repositoryCandidates: (id, code, state) => request("POST", `/accounts/${seg(id)}/github/candidates`, { code, state }),
    repositories: (id) => request("GET", `/accounts/${seg(id)}/repositories`),
    registerRepository: (id, proof, repository_id) => request("POST", `/accounts/${seg(id)}/repositories`, { proof, repository_id }),
    removeRepository: (id, repositoryId) => request("DELETE", `/accounts/${seg(id)}/repositories/${repositoryId}`),
    modelCredential: (id) => request("GET", `/accounts/${seg(id)}/model-credential`),
    saveModelCredential: (id, input) => request("PUT", `/accounts/${seg(id)}/model-credential`, input),
    deleteModelCredential: (id) => request("DELETE", `/accounts/${seg(id)}/model-credential`),
    issueCandidates: (id, repositoryId, exclude) => request("POST", `/accounts/${seg(id)}/repositories/${repositoryId}/issue-candidates`, { exclude }),
    startSprintRun: (id, repository_id, issues) => request("POST", `/accounts/${seg(id)}/sprint-runs`, { repository_id, issues }),
    sprintRuns: (id, repositoryId) =>
      request("GET", `/accounts/${seg(id)}/sprint-runs${repositoryId === undefined ? "" : `?repository_id=${repositoryId}`}`),
    // 経路は 1 件ずつ読む
    sprintRunDetails: (id, sprintRunIds) =>
      Promise.all(sprintRunIds.map((sprintRunId) => request<SprintRunDetail>("GET", `/accounts/${seg(id)}/sprint-runs/${seg(sprintRunId)}`))),
    stepDetails: (id, sprintRunId, stepKeys) =>
      Promise.all(stepKeys.map((key) => request<StepDetail>("GET", `/accounts/${seg(id)}/sprint-runs/${seg(sprintRunId)}/steps/${seg(key)}`))),
    backlogs: (id, repositoryIds) => Promise.all(repositoryIds.map((repositoryId) => request<Backlog>("GET", `/accounts/${seg(id)}/repositories/${repositoryId}/backlog`))),
    cancelSprintRun: (id, sprintRunId) => request("POST", `/accounts/${seg(id)}/sprint-runs/${seg(sprintRunId)}/cancel`),
    updateRepository: (id, repositoryId, max_concurrent_runs) => request("PATCH", `/accounts/${seg(id)}/repositories/${repositoryId}`, { max_concurrent_runs }),
    loops: (id, repositoryId) => request("GET", `/accounts/${seg(id)}/repositories/${repositoryId}/loops`),
    startLoop: (id, repositoryId, input) => request("POST", `/accounts/${seg(id)}/repositories/${repositoryId}/loops`, input),
    // 経路は 1 件ずつ読む。404 の ID は要素を返さない
    loopDetails: async (id, loopIds) => {
      const found = await Promise.all(
        loopIds.map((loopId) =>
          request<LoopDetail>("GET", `/accounts/${seg(id)}/loops/${seg(loopId)}`).catch((err: unknown) => {
            if (err instanceof ApiFailure && err.status === 404) return null;
            throw err;
          }),
        ),
      );
      return found.filter((d): d is LoopDetail => d !== null);
    },
    updateLoopBudget: (id, loopId, budget) => request("PATCH", `/accounts/${seg(id)}/loops/${seg(loopId)}`, { budget }),
    cancelLoop: (id, loopId) => request("POST", `/accounts/${seg(id)}/loops/${seg(loopId)}/cancel`),
    requestNextLap: (id, loopId) => request("POST", `/accounts/${seg(id)}/loops/${seg(loopId)}/next-lap`),
    answerLoop: (id, loopId, issue, decision, pr) =>
      request("POST", `/accounts/${seg(id)}/loops/${seg(loopId)}/answers`, { issue, decision, ...(pr === undefined ? {} : { pr }) }),
  };
}
