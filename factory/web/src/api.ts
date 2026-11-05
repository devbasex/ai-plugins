import type { Auth } from "./auth.ts";
import type { StepDetailBody } from "@ndf-factory/contracts/views";
import { MAX_ITEMS, operation, operationUrl, type OperationId } from "@ndf-factory/contracts/operations";

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

// 実行の終わりの状態（API の TERMINAL_RUN_STATES と同じ）。終わった実行の中身はもう変わらない
const FINISHED_RUN_STATES: ReadonlySet<string> = new Set(["done", "stopped", "failed", "cancelled"]);
export const isFinishedRun = (state: string) => FINISHED_RUN_STATES.has(state);

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

// エージェントへの委任（factory/contracts/openapi/delegations.yaml。#2384）
export type DelegationScope = "read" | "read_write";

export interface Delegation {
  credential_id: string;
  client_name: string;
  scope: DelegationScope;
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
  state: "active" | "expired" | "revoked";
}

export interface DelegationInput {
  client_name: string;
  scope: DelegationScope;
  expires_in_seconds: number;
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
  // 自分が発行した委任の資格情報（新しい順）
  delegations(): Promise<Delegation[]>;
  // 値（credential）は発行の結果で 1 度だけ返る
  createDelegation(input: DelegationInput): Promise<{ delegation: Delegation; credential: string | null }>;
  revokeDelegation(credentialId: string): Promise<Delegation>;
}

// 要素の結果（#2384）。入力と同じ順・同じ数で返る
type ElementResult<T> = { ok: true; value: T } | { ok: false; status: number; error: { code: string; message: string; details?: Record<string, unknown> } };

function valueOf<T>(r: ElementResult<T> | undefined): T {
  if (!r) throw new ApiFailure(503, "error", "");
  if (r.ok) return r.value;
  throw new ApiFailure(r.status, r.error.code, r.error.message, r.error.details ?? null);
}

// 1 回の要求で受け渡す要素の上限ごとに分ける
function chunks<T>(items: T[]): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += MAX_ITEMS) out.push(items.slice(i, i + MAX_ITEMS));
  return out;
}

const newKey = () => crypto.randomUUID();

// 要求は操作の表から組み、経路の文字列を直に書かない（#2384 の受け入れ条件 1）。
// 401 では token を消す。画面は ApiFailure の status を見てログインへ移る
export function createApi(apiUrl: string, auth: Auth, doFetch: typeof fetch = (...a) => fetch(...a)): Api {
  async function request<T>(id: OperationId, accountId: string | null, opts: { query?: Record<string, unknown>; items?: unknown[] } = {}): Promise<T> {
    const token = auth.getToken();
    if (!token) throw new ApiFailure(401, "unauthorized", "unauthorized");
    const op = operation(id);
    const res = await doFetch(`${apiUrl}${operationUrl(op, accountId, opts.query)}`, {
      method: op.method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(opts.items === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: opts.items === undefined ? undefined : JSON.stringify(opts.items),
    });
    const data = (await res.json().catch(() => null)) as { error?: { code: string; message: string; details?: Record<string, unknown> } } | null;
    if (!res.ok) {
      if (res.status === 401) auth.clearToken();
      throw new ApiFailure(res.status, data?.error?.code ?? "error", data?.error?.message ?? "", data?.error?.details ?? null);
    }
    return data as T;
  }

  // 一覧（条件で集めた値の配列）
  const list = <T>(id: OperationId, accountId: string | null, query?: Record<string, unknown>) => request<T[]>(id, accountId, { query });
  // 要素 1 個を送り、その結果の値を返す。失敗の要素は ApiFailure にする
  const one = async <T>(id: OperationId, accountId: string | null, item: Record<string, unknown>) =>
    valueOf((await request<ElementResult<T>[]>(id, accountId, { items: [item] }))[0]);
  // 作成（新しい識別子を作る操作）。冪等の鍵は送る中身ごとに持ち、結果が分からない失敗（503・通信の失敗）の後に
  // 同じ中身を送り直すときは同じ鍵を使う（保存の後に記録の追記が落ちた 503 で、作り直しを 2 重にしない。#2384 の決定 4）。
  // 成功か、断りの結果（503 以外の失敗）が届いたら鍵を捨て、次の送信は新しい作成にする
  const pendingKeys = new Map<string, string>();
  const create = async <T>(id: OperationId, accountId: string | null, item: Record<string, unknown>) => {
    const slot = JSON.stringify([id, accountId, item]);
    const idempotency_key = pendingKeys.get(slot) ?? newKey();
    pendingKeys.set(slot, idempotency_key);
    try {
      const value = await one<T>(id, accountId, { ...item, idempotency_key });
      pendingKeys.delete(slot);
      return value;
    } catch (err) {
      if (err instanceof ApiFailure && err.status !== 503) pendingKeys.delete(slot);
      throw err;
    }
  };
  // 鍵の query で指す取得と削除。要素の結果を入力と同じ順に返す
  const select = async <T>(id: OperationId, accountId: string | null, key: string, values: (string | number)[], query: Record<string, unknown> = {}) => {
    const out: ElementResult<T>[] = [];
    for (const part of chunks(values)) out.push(...(await request<ElementResult<T>[]>(id, accountId, { query: { ...query, [key]: part } })));
    return out;
  };
  // 鍵の query で指す要素の値の配列。最初の失敗の要素で投げる
  const values = async <T>(id: OperationId, accountId: string | null, key: string, ids: (string | number)[], query?: Record<string, unknown>) =>
    (await select<T>(id, accountId, key, ids, query)).map((r) => valueOf(r));

  return {
    me: async () => (await list<Me>("getMe", null))[0]!,
    account: async (id) => (await values<AccountDetail>("getAccount", null, "account_id", [id]))[0]!,
    createTeam: (name, seat_type) => create("createTeam", null, { name, seat_type }),
    setExecutionMode: (id, execution_mode) => one("setExecutionMode", null, { account_id: id, execution_mode }),
    addSeat: (id, user_id, seat_type) => one("addSeat", id, { user_id, seat_type }),
    changeSeat: (id, user_id, seat_type) => one("changeSeat", id, { user_id, seat_type }),
    removeSeat: async (id, userId) => {
      await values("removeSeat", id, "user_id", [userId]);
    },
    installUrl: async (id) => (await list<{ url: string }>("getInstallUrl", id))[0]!,
    authorizeUrl: async (id) => (await list<{ url: string }>("getAuthorizeUrl", id))[0]!,
    repositoryCandidates: (id, code, state) => one("postRepositoryCandidates", id, { code, state }),
    repositories: async (id) => ({ repositories: await list<Repository>("listRepositories", id) }),
    registerRepository: (id, proof, repository_id) => one("registerRepository", id, { proof, repository_id }),
    removeRepository: async (id, repositoryId) => {
      await values("removeRepository", id, "repository_id", [repositoryId]);
    },
    modelCredential: async (id) => (await list<ModelCredential>("getModelCredential", id))[0]!,
    saveModelCredential: (id, input) => one("putModelCredential", id, input),
    deleteModelCredential: async (id) => {
      valueOf((await request<ElementResult<unknown>[]>("deleteModelCredential", id))[0]);
    },
    issueCandidates: (id, repository_id, exclude) => one("postIssueCandidates", id, { repository_id, exclude }),
    startSprintRun: (id, repository_id, issues) => create("startSprintRun", id, { repository_id, issues }),
    sprintRuns: async (id, repositoryId) => ({
      sprint_runs: await list<SprintRunRow>("listSprintRuns", id, repositoryId === undefined ? {} : { repository_id: repositoryId }),
    }),
    // 要素の数によらず 1 回の要求で読む。無い実行は他人の実行と同じ forbidden の要素で、そのまま投げる
    sprintRunDetails: (id, sprintRunIds) => values<SprintRunDetail>("getSprintRunDetail", id, "sprint_run_id", sprintRunIds),
    stepDetails: (id, sprintRunId, stepKeys) => values<StepDetail>("getStepDetail", id, "step_key", stepKeys, { sprint_run_id: sprintRunId }),
    backlogs: (id, repositoryIds) => values<Backlog>("getBacklog", id, "repository_id", repositoryIds),
    cancelSprintRun: (id, sprint_run_id) => one("cancelSprintRun", id, { sprint_run_id }),
    updateRepository: (id, repository_id, max_concurrent_runs) => one("updateRepository", id, { repository_id, max_concurrent_runs }),
    loops: async (id, repositoryId) => ({ loops: await list<LoopRow>("listLoops", id, { repository_id: repositoryId }) }),
    startLoop: (id, repository_id, input) => create("startLoop", id, { repository_id, ...input }),
    // 要素の数によらず 1 回の要求で読む。無いループ（loop_not_found）の要素は返さない
    loopDetails: async (id, loopIds) => {
      const results = await select<LoopDetail>("getLoop", id, "loop_id", loopIds);
      return results.filter((r) => r.ok || r.status !== 404).map((r) => valueOf(r));
    },
    updateLoopBudget: (id, loop_id, budget) => one("updateLoopBudget", id, { loop_id, budget }),
    cancelLoop: (id, loop_id) => one("cancelLoop", id, { loop_id }),
    requestNextLap: (id, loop_id) => one("startNextLap", id, { loop_id }),
    answerLoop: (id, loop_id, issue, decision, pr) => one("answerLoopPending", id, { loop_id, issue, decision, ...(pr === undefined ? {} : { pr }) }),
    delegations: () => list<Delegation>("listDelegations", null),
    createDelegation: (input) => create("createDelegation", null, { ...input }),
    revokeDelegation: async (credentialId) => (await values<Delegation>("revokeDelegation", null, "credential_id", [credentialId]))[0]!,
  };
}
