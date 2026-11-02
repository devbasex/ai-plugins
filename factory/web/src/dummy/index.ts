import type { Auth } from "../auth.ts";
import { fixedConnection, type ConnectionState } from "../connection.ts";
import type { Services } from "../context.tsx";
import { createDummyApi, type Scenario } from "./api.ts";
import { DUMMY_MARKER } from "./data.ts";

export { createDummyApi } from "./api.ts";

const CONNECTIONS: Record<string, ConnectionState> = {
  online: { online: true, lastReceivedAt: null, pendingOperations: 0 },
  offline: { online: false, lastReceivedAt: "2026-11-02T00:00:00Z", pendingOperations: 0 },
  pending: { online: true, lastReceivedAt: "2026-11-02T00:00:00Z", pendingOperations: 2 },
};

function scenarioOf(value: string | null): Scenario {
  return value === "empty" || value === "error" ? value : "normal";
}

// ログインしたままの Auth。ログアウトすると token が消え、ログインで戻る
export function createDummyAuth(): Auth {
  let token: string | null = DUMMY_MARKER;
  return {
    login: async () => {
      token = DUMMY_MARKER;
      window.location.assign("/account");
    },
    completeLogin: async () => {
      token = DUMMY_MARKER;
      return { ok: true };
    },
    getToken: () => token,
    clearToken: () => {
      token = null;
    },
    logout: () => {
      token = null;
    },
  };
}

// 場面（?scenario=normal|empty|error）とつながり（?connection=online|offline|pending）を検索の値から決める
export function createDummyServices(search: string): Services {
  const params = new URLSearchParams(search);
  return {
    auth: createDummyAuth(),
    api: createDummyApi(scenarioOf(params.get("scenario"))),
    connection: fixedConnection(CONNECTIONS[params.get("connection") ?? "online"] ?? CONNECTIONS.online!),
  };
}
