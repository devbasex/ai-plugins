import type { Auth } from "./auth.ts";

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
  sprintRuns(id: string, repositoryId: number): Promise<{ sprint_runs: SprintRunSummary[] }>;
  cancelSprintRun(id: string, sprintRunId: string): Promise<{ state: string }>;
}

// 401 では token を消す。画面は ApiFailure の status を見てログインへ移る
export function createApi(apiUrl: string, auth: Auth, doFetch: typeof fetch = (...a) => fetch(...a)): Api {
  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const token = auth.getToken();
    if (!token) throw new ApiFailure(401, "unauthorized", "ログインしてください");
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
    sprintRuns: (id, repositoryId) => request("GET", `/accounts/${seg(id)}/sprint-runs?repository_id=${repositoryId}`),
    cancelSprintRun: (id, sprintRunId) => request("POST", `/accounts/${seg(id)}/sprint-runs/${seg(sprintRunId)}/cancel`),
  };
}
