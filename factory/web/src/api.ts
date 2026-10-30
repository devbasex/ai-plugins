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
  ) {
    super(message);
  }
}

export interface Api {
  me(): Promise<Me>;
  account(id: string): Promise<AccountDetail>;
  createTeam(name: string, seatType: SeatType): Promise<AccountDetail>;
  setExecutionMode(id: string, mode: ExecutionMode): Promise<AccountDetail>;
  addSeat(id: string, userId: string, seatType: SeatType): Promise<Seat>;
  changeSeat(id: string, userId: string, seatType: SeatType): Promise<Seat>;
  removeSeat(id: string, userId: string): Promise<void>;
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
    const data = (await res.json().catch(() => null)) as { error?: { code: string; message: string } } | null;
    if (!res.ok) {
      if (res.status === 401) auth.clearToken();
      throw new ApiFailure(res.status, data?.error?.code ?? "error", data?.error?.message ?? "");
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
  };
}
