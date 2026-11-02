import type { AccountDetail, IssueCandidates, LoopDetail, Me, ModelCredential, Repository, SprintRunSummary } from "../api.ts";

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

// 開発ループ。待ち・止まった・人の判断待ち・利用上限の待ちの見え方がそろう組
export const loopDetails: LoopDetail[] = [
  {
    loop: {
      loop_id: "lp-0001",
      name: "lfactory",
      milestones: ["01 見本"],
      state: "running",
      wait_reason: null,
      laps_closed: 2,
      cost_usd: 12.5,
      budget: { laps: 10, usd: 100, hours: null },
      budget_remaining: { laps: 8, usd: 87.5, hours: null },
      last_run_at: "2026-11-01T23:30:00Z",
      stop_condition: null,
      stop_detail: null,
    },
    laps: [
      { lap: 1, issues: [3, 4, 5], result: "完了", version: "1.2.0", cost_usd: 7.25, seconds: 5400 },
      { lap: 2, issues: [8], result: "人へ戻した", version: null, cost_usd: 5.25, seconds: 3600 },
    ],
    runs: [
      { sprint_run_id: "sr-0003", lap: 3, state: "awaiting_reset", failure_reason: null, finish_reason: "usage_limit", resume_at: "2026-11-02T03:00:00Z", cost_usd: 0, sections: 4 },
      { sprint_run_id: "sr-0001", lap: 1, state: "done", failure_reason: null, finish_reason: null, resume_at: null, cost_usd: 7.25, sections: 3 },
    ],
    pending: [{ number: 22, reason: "MVV 判定が「従う」以外", lap: 2, answer: null }],
  },
  {
    loop: {
      loop_id: "lp-0002",
      name: "lroot",
      milestones: ["02 見本"],
      state: "between_laps",
      wait_reason: "user_start",
      laps_closed: 1,
      cost_usd: 3,
      budget: {},
      budget_remaining: {},
      last_run_at: "2026-10-31T10:00:00Z",
      stop_condition: null,
      stop_detail: null,
    },
    laps: [{ lap: 1, issues: [30], result: "完了", version: "1.1.0", cost_usd: 3, seconds: 1800 }],
    runs: [],
    pending: [],
  },
  {
    loop: {
      loop_id: "lp-0003",
      name: "lflow",
      milestones: ["03 見本"],
      state: "stopped",
      wait_reason: null,
      laps_closed: 5,
      cost_usd: 50,
      budget: { usd: 50 },
      budget_remaining: { usd: 0 },
      last_run_at: "2026-10-30T01:15:00Z",
      stop_condition: "budget_usd",
      stop_detail: '{"spent":50,"limit":50}',
    },
    laps: [],
    runs: [],
    pending: [],
  },
];
