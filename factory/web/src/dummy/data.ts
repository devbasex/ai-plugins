import type { AccountDetail, ApprovalRequestRow, Backlog, Delegation, IssueCandidates, LoopDetail, Me, ModelCredential, Repository, SprintRunDetail, SprintRunRow, StepDetail, StepRow } from "../api.ts";

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

const REPO_A = "example-org/sample-service";
const REPO_B = "example-org/a-repository-with-a-very-long-name-for-narrow-screens";

export const sprintRuns: SprintRunRow[] = [
  {
    sprint_run_id: "sr-0003",
    repository_id: 101,
    issues: [12, 15],
    state: "awaiting_approval",
    failure_reason: null,
    created_at: "2026-11-01T23:30:00Z",
    repo: REPO_A,
    current_phase: "実装",
    current_step: "impl",
    last_event_at: "2026-11-02T00:20:00Z",
  },
  {
    sprint_run_id: "sr-0002",
    repository_id: 102,
    issues: [8],
    state: "failed",
    failure_reason: "timeout",
    created_at: "2026-10-31T10:00:00Z",
    repo: REPO_B,
    current_phase: null,
    current_step: null,
    last_event_at: null,
  },
  {
    sprint_run_id: "sr-0001",
    repository_id: 101,
    issues: [3, 4, 5],
    state: "done",
    failure_reason: null,
    created_at: "2026-10-30T01:15:00Z",
    repo: REPO_A,
    current_phase: "仕上げ",
    current_step: "refine",
    last_event_at: "2026-10-30T05:00:00Z",
  },
];

// 工程の記録の行。key は job:連番、費用は結べたものだけ
function step(key: string, at: string, name: string, phase: string, issues: number[], patch: Partial<StepRow> = {}): StepRow {
  return {
    key,
    at,
    job_id: key.split(":")[0]!,
    step: name,
    phase,
    issues,
    exit_code: 0,
    seconds: 120,
    cost_usd: null,
    summary: `${name} の要約（見本）`,
    pull_request: null,
    has_details: false,
    ...patch,
  };
}

const PHASES = ["設計", "実装", "検査", "取り込み", "仕上げ"];

function detailOf(row: SprintRunRow, steps: StepRow[], patch: Partial<SprintRunDetail> = {}): SprintRunDetail {
  const keysOf = (pred: (s: StepRow) => boolean) => steps.filter(pred).map((s) => s.key);
  const last = (name: string) => {
    const s = steps.filter((x) => x.step === name).at(-1);
    return s ? { key: s.key, exit_code: s.exit_code, seconds: s.seconds } : null;
  };
  const pr = (issue: number | null) =>
    steps
      .filter((s) => s.pull_request && (issue === null ? s.issues.length === 0 : s.issues.includes(issue)))
      .map((s) => ({ ...s.pull_request!, state: "open" as const, checks: [{ name: "factory-check", conclusion: "success" }, { name: "lint", conclusion: null }] }));
  return {
    sprint_run_id: row.sprint_run_id,
    repository_id: row.repository_id,
    issues: row.issues,
    state: row.state,
    failure_reason: row.failure_reason,
    created_at: row.created_at,
    repo: row.repo,
    ndf_version: "10.27.15",
    execution_mode: "byok",
    job_count: 1,
    current_phase: row.current_phase,
    phases: PHASES.map((name) => ({
      name,
      step_keys: keysOf((s) => s.phase === name),
      required: name === "仕上げ" ? [{ label: "振り返り", step: "retro", record: last("retro") }, { label: "棚卸し", step: "refine", record: last("refine") }] : [],
    })),
    columns: [...row.issues.map((issue) => ({ issue, step_keys: keysOf((s) => s.issues.includes(issue)), pull_requests: pr(issue) })), { issue: null, step_keys: keysOf((s) => s.issues.length === 0), pull_requests: pr(null) }],
    steps,
    gates: { gate1: { state: "not_reached", at: null }, gate2: { state: "not_reached", at: null }, decision: { state: "none", at: null } },
    delegated_decisions: 0,
    usage: { input_tokens: 1_250_000, output_tokens: 48_000, cost_usd: 3.75 },
    omitted_steps: 0,
    attention: [],
    ...patch,
  };
}

const sr3Steps = [
  step("job-a:1", "2026-11-01T23:40:00Z", "design", "設計", [12], { cost_usd: 1.25, has_details: true, pull_request: { repo: REPO_A, number: 41, url: `https://github.com/${REPO_A}/pull/41` } }),
  step("job-a:2", "2026-11-01T23:45:00Z", "design", "設計", [15], { cost_usd: 1.1 }),
  step("job-a:3", "2026-11-01T23:50:00Z", "sprint-branch", "実装", []),
  step("job-a:4", "2026-11-02T00:20:00Z", "impl", "実装", [12], { cost_usd: 1.4, seconds: 1800, has_details: true }),
];

const sr1Steps = [
  step("job-c:1", "2026-10-30T02:00:00Z", "impl", "実装", [3, 4, 5], { cost_usd: 2 }),
  step("job-c:2", "2026-10-30T03:00:00Z", "check", "検査", []),
  step("job-c:3", "2026-10-30T04:00:00Z", "release", "取り込み", []),
  step("job-c:4", "2026-10-30T04:30:00Z", "retro", "仕上げ", [], { seconds: 300 }),
  step("job-c:5", "2026-10-30T05:00:00Z", "refine", "仕上げ", [], { seconds: 240, summary: "順位を見直した（見本）" }),
];

export const sprintRunDetails: SprintRunDetail[] = [
  detailOf(sprintRuns[0]!, sr3Steps, {
    gates: { gate1: { state: "waiting", at: "2026-11-02T00:20:00Z" }, gate2: { state: "not_reached", at: null }, decision: { state: "none", at: null } },
    delegated_decisions: 2,
  }),
  // 取り込みで解けない衝突で止まった実行（#2430）
  detailOf(sprintRuns[1]!, [], {
    attention: [{ key: "job-b:attention:1", at: "2026-10-31T10:30:00Z", reason: "base_conflict", summary: "src/a.ts で衝突（見本）" }],
  }),
  detailOf(sprintRuns[2]!, sr1Steps, {
    gates: { gate1: { state: "approved", at: "2026-10-30T01:50:00Z" }, gate2: { state: "approved", at: "2026-10-30T04:10:00Z" }, decision: { state: "none", at: null } },
  }),
];

// 承認待ちの一覧（#2430）。承認を待つ実行の答えの無い承認の依頼
export const approvalRequests: ApprovalRequestRow[] = [
  {
    sprint_run_id: sprintRuns[0]!.sprint_run_id,
    repository_id: sprintRuns[0]!.repository_id,
    repo: sprintRuns[0]!.repo,
    issues: sprintRuns[0]!.issues,
    kind: "gate1",
    loop: { name: "lfactory2", lap: 3 },
    waiting_since: "2026-11-02T00:20:00Z",
  },
];

const sha = (c: string) => c.repeat(40);

// 工程の中身。中身を持たない工程は欄がどれも null
export function stepDetailOf(sprintRunId: string, key: string): StepDetail | null {
  const row = sprintRunDetails.find((d) => d.sprint_run_id === sprintRunId)?.steps.find((s) => s.key === key);
  if (!row) return null;
  if (!row.has_details) return { ...row, files: null, files_omitted: null, commit_range: null, decisions: null, output: null, diff_url: null };
  const range = { from: sha("a"), to: sha("b") };
  return {
    ...row,
    files: [
      { path: "src/service/handler.ts", added: 42, deleted: 7, url: `https://github.com/${REPO_A}/blob/${range.to}/src/service/handler.ts` },
      { path: "docs/design.md", added: 120, deleted: 0, url: `https://github.com/${REPO_A}/blob/${range.to}/docs/design.md` },
    ],
    files_omitted: null,
    commit_range: range,
    decisions: [{ question: "見本の問い: 一覧の並びはどちらにするか", answer: "新しい順にする", by: "ai" }],
    output: { kind: "report", text: "## 作業の報告（見本）\n- 作業: 実装\n- 結果: 完了\n", truncated: false },
    diff_url: row.pull_request ? `${row.pull_request.url}/files` : `https://github.com/${REPO_A}/compare/${range.from}...${range.to}`,
  };
}

export const backlogs: Backlog[] = [
  {
    repository_id: 101,
    repo: REPO_A,
    ranking: {
      milestone: "01 見本",
      rows: [
        { rank: 1, number: 21, title: "見本の課題 1" },
        { rank: 2, number: 22, title: "見本の課題 2" },
      ],
    },
    refinements: [{ sprint_run_id: "sr-0001", key: "job-c:5", at: "2026-10-30T05:00:00Z", exit_code: 0, summary: "順位を見直した（見本）", pull_request: null }],
  },
  { repository_id: 102, repo: REPO_B, ranking: null, refinements: [] },
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

// 委任の資格情報の一覧（値は持たない）
export const delegations: Delegation[] = [
  {
    credential_id: "dummy000000000000000000001",
    client_name: "見本の Claude Code",
    scope: "read",
    created_at: "2026-11-04T00:00:00.000Z",
    expires_at: "2026-11-04T01:00:00.000Z",
    revoked_at: null,
    state: "expired",
  },
];
