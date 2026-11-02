import type { AccountDetail, IssueCandidates, Me, ModelCredential, Repository, SprintRunSummary } from "../api.ts";

// ダミーデータ。本番の成果物にこの印が出たら check:bundle が落とす。実在の人・秘密を書かない
export const DUMMY_MARKER = "ndf-factory-dummy-data";

export const PERSONAL_ID = "00000000-0000-4000-8000-000000000001";
export const TEAM_ID = "00000000-0000-4000-8000-000000000002";
export const USER_ID = "00000000-0000-4000-8000-0000000000aa";

export const me: Me = {
  user_id: USER_ID,
  idp: "github",
  personal_account_id: PERSONAL_ID,
  accounts: [
    { account_id: PERSONAL_ID, kind: "personal", name: null, plan: "pro", role: "owner", seat_type: null, execution_mode: "byok" },
    { account_id: TEAM_ID, kind: "team", name: "見本のチーム", plan: "team", role: "owner", seat_type: "premium", execution_mode: "managed" },
  ],
};

export const accounts: Record<string, AccountDetail> = {
  [PERSONAL_ID]: { ...me.accounts[0]!, limits: { concurrent_sprint_runs: 2, monthly_tokens: null }, seats: null },
  [TEAM_ID]: {
    ...me.accounts[1]!,
    limits: { concurrent_sprint_runs: 10, monthly_tokens: 125_000_000 },
    seats: [
      { user_id: USER_ID, idp: "github", seat_type: "premium", added_at: "2026-10-30T00:00:00Z" },
      { user_id: "00000000-0000-4000-8000-0000000000bb", idp: "google", seat_type: "standard", added_at: "2026-10-31T03:00:00Z" },
    ],
  },
};

export const repositories: Repository[] = [
  { repository_id: 101, full_name: "example-org/sample-service", installation_id: 9001, registered_at: "2026-10-20T00:00:00Z", active_sprint_runs: 1 },
  { repository_id: 102, full_name: "example-org/a-repository-with-a-very-long-name-for-narrow-screens", installation_id: 9001, registered_at: "2026-10-21T00:00:00Z", active_sprint_runs: 0 },
];

export const sprintRuns: SprintRunSummary[] = [
  { sprint_run_id: "sr-0003", repository_id: 101, issues: [12, 15], state: "awaiting_approval", failure_reason: null, created_at: "2026-11-01T23:30:00Z" },
  { sprint_run_id: "sr-0002", repository_id: 102, issues: [8], state: "failed", failure_reason: "timeout", created_at: "2026-10-31T10:00:00Z" },
  { sprint_run_id: "sr-0001", repository_id: 101, issues: [3, 4, 5], state: "done", failure_reason: null, created_at: "2026-10-30T01:15:00Z" },
];

export const issueCandidates: IssueCandidates = {
  source: "ranking",
  milestone: "01 見本",
  note: null,
  candidates: [
    { number: 21, title: "見本の課題 1", selected: true, reason: "順位が高い" },
    { number: 22, title: "見本の課題 2", selected: false, reason: "後でよい" },
  ],
};

export const modelCredential: ModelCredential = {
  registered: true,
  kind: "anthropic_api_key",
  updated_at: "2026-10-29T08:00:00Z",
  external_id: `ndf-factory:${PERSONAL_ID}`,
};
