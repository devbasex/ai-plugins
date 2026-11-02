import { ApiFailure, type Api } from "../api.ts";
import * as data from "./data.ts";

// 場面: normal は決まったデータ、empty は一覧が 0 件、error は読み込みがすべて失敗する
export type Scenario = "normal" | "empty" | "error";

export function createDummyApi(scenario: Scenario = "normal"): Api {
  const read = async <T>(value: () => T): Promise<T> => {
    if (scenario === "error") throw new ApiFailure(503, "unavailable", data.DUMMY_MARKER);
    return structuredClone(value());
  };
  const list = <T>(items: T[]): T[] => (scenario === "empty" ? [] : items);
  const account = (id: string) => {
    const found = data.accounts[id];
    if (!found) throw new ApiFailure(403, "forbidden", "forbidden");
    return found;
  };
  return {
    me: () => read(() => data.me),
    account: (id) => read(() => account(id)),
    createTeam: async () => read(() => account(data.TEAM_ID)),
    setExecutionMode: async (id, mode) => read(() => ({ ...account(id), execution_mode: mode })),
    addSeat: async (_id, userId, seatType) => read(() => ({ user_id: userId, idp: null, seat_type: seatType, added_at: new Date().toISOString() })),
    changeSeat: async (_id, userId, seatType) => read(() => ({ user_id: userId, idp: null, seat_type: seatType, added_at: new Date().toISOString() })),
    removeSeat: async () => undefined,
    installUrl: () => read(() => ({ url: "https://github.com/apps/example/installations/new" })),
    authorizeUrl: () => read(() => ({ url: "/accounts" })),
    repositoryCandidates: () => read(() => ({ proof: "dummy-proof", candidates: [] })),
    repositories: () => read(() => ({ repositories: list(data.repositories) })),
    registerRepository: (_id, _proof, repositoryId) => read(() => data.repositories.find((r) => r.repository_id === repositoryId) ?? data.repositories[0]!),
    removeRepository: async () => undefined,
    modelCredential: () => read(() => data.modelCredential),
    saveModelCredential: (_id, input) => read(() => ({ ...data.modelCredential, kind: input.kind, updated_at: new Date().toISOString() })),
    deleteModelCredential: async () => undefined,
    issueCandidates: () => read(() => ({ ...data.issueCandidates, candidates: list(data.issueCandidates.candidates) })),
    startSprintRun: () => read(() => ({ sprint_run_id: "sr-new", state: "starting" })),
    sprintRuns: (_id, repositoryId) =>
      read(() => ({ sprint_runs: list(data.sprintRuns).filter((r) => repositoryId === undefined || r.repository_id === repositoryId) })),
    sprintRunDetails: (_id, ids) => read(() => list(data.sprintRuns).filter((r) => ids.includes(r.sprint_run_id))),
    cancelSprintRun: () => read(() => ({ state: "cancelled" })),
  };
}
