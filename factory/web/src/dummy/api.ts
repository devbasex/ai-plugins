import { ApiFailure, type Api, type Delegation } from "../api.ts";
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
  // 発行した委任はこの画面の間だけ持つ（値はダミーで、本物の API へは届かない）
  const delegations: Delegation[] = scenario === "empty" ? [] : structuredClone(data.delegations);
  const loopOf = (loopId: string) => {
    const found = data.loopDetails.find((d) => d.loop.loop_id === loopId);
    if (!found) throw new ApiFailure(404, "loop_not_found", "loop_not_found");
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
    // 無い実行は API と同じく 403
    sprintRunDetails: (_id, ids) =>
      read(() =>
        ids.map((id) => {
          const found = list(data.sprintRunDetails).find((d) => d.sprint_run_id === id);
          if (!found) throw new ApiFailure(403, "forbidden", "forbidden");
          return found;
        }),
      ),
    stepDetails: (_id, sprintRunId, keys) =>
      read(() =>
        keys.map((key) => {
          const found = data.stepDetailOf(sprintRunId, key);
          if (!found) throw new ApiFailure(403, "forbidden", "forbidden");
          return found;
        }),
      ),
    backlogs: (_id, repositoryIds) =>
      read(() =>
        repositoryIds.map((repositoryId) => {
          const found = data.backlogs.find((b) => b.repository_id === repositoryId);
          if (!found) throw new ApiFailure(403, "forbidden", "forbidden");
          return scenario === "empty" ? { ...found, ranking: null, refinements: [] } : found;
        }),
      ),
    cancelSprintRun: () => read(() => ({ state: "cancelled" })),
    approvalRequests: () => read(() => list(data.approvalRequests)),
    updateRepository: (_id, repositoryId, max) =>
      read(() => ({ ...(data.repositories.find((r) => r.repository_id === repositoryId) ?? data.repositories[0]!), max_concurrent_runs: max })),
    // ダミーのループは最初のリポジトリにだけ属する
    loops: (_id, repositoryId) => read(() => ({ loops: repositoryId === data.repositories[0]!.repository_id ? list(data.loopDetails).map((d) => d.loop) : [] })),
    startLoop: () => read(() => ({ loop_id: "lp-new", state: "running" as const })),
    loopDetails: (_id, ids) => read(() => list(data.loopDetails).filter((d) => ids.includes(d.loop.loop_id))),
    updateLoopBudget: (_id, loopId, budget) => read(() => ({ ...loopOf(loopId).loop, budget })),
    cancelLoop: () => read(() => ({ state: "cancelled" as const })),
    requestNextLap: () => read(() => ({ state: "running" as const })),
    answerLoop: (_id, loopId, issue, decision, pr) =>
      read(() => ({ pending: loopOf(loopId).pending.map((p) => (p.number === issue ? { ...p, answer: { decision, pr: pr ?? null } } : p)) })),
    delegations: () => read(() => delegations),
    createDelegation: async (input) => {
      const now = new Date();
      const delegation: Delegation = {
        credential_id: `dummy${String(delegations.length + 1).padStart(21, "0")}`,
        client_name: input.client_name,
        scope: input.scope,
        created_at: now.toISOString(),
        expires_at: new Date(now.getTime() + input.expires_in_seconds * 1000).toISOString(),
        revoked_at: null,
        state: "active",
      };
      delegations.unshift(delegation);
      return read(() => ({ delegation, credential: `ndfd_${delegation.credential_id}_${data.DUMMY_MARKER}` }));
    },
    revokeDelegation: async (credentialId) => {
      const found = delegations.find((d) => d.credential_id === credentialId);
      if (!found) throw new ApiFailure(403, "forbidden", "forbidden");
      if (found.state === "active") Object.assign(found, { state: "revoked", revoked_at: new Date().toISOString() });
      return read(() => found);
    },
  };
}
