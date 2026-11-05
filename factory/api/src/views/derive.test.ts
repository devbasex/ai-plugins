import { describe, expect, it } from "vitest";
import type { ProgressEvent } from "@ndf-factory/contracts";
import { toRecord } from "../progress/append.ts";
import { awaitingGateOf, capSteps, latestOf, type StepRecord } from "../progress/read.ts";
import type { ProgressEventRecord } from "../progress/store.ts";
import type { SprintRun } from "../runs/model.ts";
import {
  approvalRequestOf,
  attentionsOf,
  deriveDetail,
  deriveRefinements,
  deriveStepDetail,
  gatesOf,
  listRowExtras,
  sortApprovalRequests,
  stepRows,
} from "./derive.ts";

const SHA = (c: string) => c.repeat(40);

function runOf(patch: Partial<SprintRun> = {}): SprintRun {
  return {
    sprintRunId: "sr-1",
    accountId: "acc-1",
    repositoryId: 101,
    repo: "org-a/repo-a",
    state: "running",
    issues: [11, 12],
    ndfVersion: "10.27.15",
    executionMode: "byok",
    modelCredentialSecretArn: null,
    currentJobId: "job-1",
    jobCount: 1,
    cancelRequested: false,
    failureReason: null,
    createdBy: "user-1",
    createdAt: "2026-11-04T00:00:00.000Z",
    updatedAt: "2026-11-04T00:00:00.000Z",
    ...patch,
  };
}

// 事象の列を作る。秒ずつ進む時刻と連番の鍵を振る
function events(sprintRunId = "sr-1") {
  const out: ProgressEventRecord[] = [];
  let n = 0;
  const add = (type: string, payload: Record<string, unknown>, opts: { job?: string; at?: string; source?: string } = {}) => {
    n += 1;
    const at = opts.at ?? new Date(Date.UTC(2026, 10, 4, 0, 0, n)).toISOString();
    const job = opts.job ?? "job-1";
    const event = {
      sprint_run_id: sprintRunId,
      idempotency_key: `${job}:${n}`,
      type,
      at,
      source: opts.source ?? "runner",
      job_id: job,
      schema_version: 1,
      payload,
    } as unknown as ProgressEvent;
    const record = toRecord(event);
    out.push(record);
    return record;
  };
  const step = (step: string, patch: Record<string, unknown> = {}, opts: { job?: string; at?: string } = {}) =>
    add("step.recorded", { step, exit_code: 0, seconds: 10, summary: `${step} の要約`, ...patch }, opts) as StepRecord;
  return { out, add, step };
}

describe("一覧の行（受け入れ条件 1〜3・I4・I5）", () => {
  it("最後の phase.changed のフェーズ・最後の工程・最後の事象の時刻を足す", () => {
    for (const last of ["設計", "検査", "仕上げ"]) {
      const e = events();
      e.add("phase.changed", { phase: "実装", issues: [] });
      e.add("phase.changed", { phase: last, issues: [] });
      e.step("impl");
      const extras = listRowExtras(runOf(), latestOf(e.out));
      expect(extras).toEqual({ repo: "org-a/repo-a", current_phase: last, current_step: "impl", last_event_at: e.out.at(-1)!.at });
    }
  });

  it("phase.changed が 0 件なら null、5 つに無い名前はそのまま返す", () => {
    expect(listRowExtras(runOf(), latestOf([]))).toEqual({ repo: "org-a/repo-a", current_phase: null, current_step: null, last_event_at: null });
    const e = events();
    e.add("phase.changed", { phase: "準備", issues: [] });
    expect(listRowExtras(runOf(), latestOf(e.out)).current_phase).toBe("準備");
  });
});

describe("詳細（受け入れ条件 5・6・8・9・I6〜I10）", () => {
  it("課題 #A・#B・番号なしの工程が、それぞれの欄に order_key の順で並び、2 つの課題を持つ工程は 2 つの欄に 1 回ずつ", () => {
    const e = events();
    const a1 = e.step("impl", { issues: [11] });
    const b1 = e.step("impl", { issues: [12] });
    const all = e.step("check");
    const both = e.step("pr", { issues: [11, 12] });
    const a2 = e.step("merge", { issues: [11] });
    const empty = e.step("sync", { issues: [] });
    const d = deriveDetail(runOf(), e.out, 0);
    expect(d.columns.map((c) => c.issue)).toEqual([11, 12, null]);
    expect(d.columns[0]!.step_keys).toEqual([a1, both, a2].map((x) => x.idempotency_key));
    expect(d.columns[1]!.step_keys).toEqual([b1, both].map((x) => x.idempotency_key));
    expect(d.columns[2]!.step_keys).toEqual([all, empty].map((x) => x.idempotency_key));
  });

  it("欄の Pull Request は最後の pr.updated の状態と、チェックの名前ごとの最後の結論を持ち、pr.updated が無ければ unknown", () => {
    const e = events();
    e.step("pr", { issues: [11], pull_request: { repo: "org-a/repo-a", number: 7 } });
    e.step("pr", { issues: [12], pull_request: { repo: "org-a/repo-a", number: 8 } });
    e.add("pr.updated", { repo: "Org-A/Repo-A", number: 7, state: "open" }, { source: "github", job: "x" });
    e.add("ci.updated", { repo: "org-a/repo-a", pr_number: 7, name: "factory-check", conclusion: "failure" }, { source: "github" });
    e.add("ci.updated", { repo: "org-a/repo-a", pr_number: 7, name: "factory-check", conclusion: "success" }, { source: "github" });
    e.add("ci.updated", { repo: "org-a/repo-a", pr_number: 7, name: "lint", conclusion: null }, { source: "github" });
    e.add("pr.updated", { repo: "org-a/repo-a", number: 7, state: "merged" }, { source: "github" });
    const d = deriveDetail(runOf(), e.out, 0);
    expect(d.columns[0]!.pull_requests).toEqual([
      {
        repo: "org-a/repo-a",
        number: 7,
        url: "https://github.com/org-a/repo-a/pull/7",
        state: "merged",
        checks: [
          { name: "factory-check", conclusion: "success" },
          { name: "lint", conclusion: null },
        ],
      },
    ]);
    expect(d.columns[1]!.pull_requests[0]).toMatchObject({ number: 8, state: "unknown", checks: [] });
  });

  it("承認の欄はゲート 1・ゲート 2・判断の 3 つで、到達・承認・差し戻し・判断の答えで状態が移る", () => {
    const e = events();
    expect(gatesOf(e.out)).toEqual({ gate1: { state: "not_reached", at: null }, gate2: { state: "not_reached", at: null }, decision: { state: "none", at: null } });
    // 到達の前の答えは状態を変えない
    e.add("gate.answered", { approval_request_id: "a0", kind: "gate1", decision: "approved", answered_by_account_id: "acc-1" }, { source: "api" });
    expect(gatesOf(e.out).gate1.state).toBe("not_reached");
    e.add("gate.reached", { kind: "gate1", pr_url: null, material: "資料" });
    expect(gatesOf(e.out).gate1.state).toBe("waiting");
    e.add("gate.answered", { approval_request_id: "a1", kind: "gate1", decision: "rejected", answered_by_account_id: "acc-1" }, { source: "api" });
    expect(gatesOf(e.out).gate1.state).toBe("rejected");
    e.add("gate.reached", { kind: "gate1", pr_url: null, material: "直した資料" });
    e.add("gate.answered", { approval_request_id: "a2", kind: "gate1", decision: "approved", answered_by_account_id: "acc-1" }, { source: "api" });
    e.add("gate.reached", { kind: "decision", pr_url: null, material: "問い" });
    const mid = gatesOf(e.out);
    expect([mid.gate1.state, mid.gate2.state, mid.decision.state]).toEqual(["approved", "not_reached", "waiting"]);
    e.add("gate.answered", { approval_request_id: "a3", kind: "decision", answered_by_account_id: "acc-1" }, { source: "api" });
    const after = gatesOf(e.out);
    expect(Object.keys(after)).toEqual(["gate1", "gate2", "decision"]);
    expect([after.gate1.state, after.decision.state]).toEqual(["approved", "answered"]);
  });

  it("仕上げの欄は振り返りと棚卸しの行を常に持ち、記録の前は null、後は終了コードと秒", () => {
    const before = deriveDetail(runOf(), [], 0);
    expect(before.phases.map((p) => p.name)).toEqual(["設計", "実装", "検査", "取り込み", "仕上げ"]);
    expect(before.phases[4]!.required).toEqual([
      { label: "振り返り", step: "retro", record: null },
      { label: "棚卸し", step: "refine", record: null },
    ]);
    expect(before.current_phase).toBeNull();
    const e = events();
    e.add("phase.changed", { phase: "仕上げ", issues: [] });
    const retro = e.step("retro", { exit_code: 0, seconds: 42 });
    const d = deriveDetail(runOf(), e.out, 0);
    expect(d.phases[4]!.required[0]!.record).toEqual({ key: retro.idempotency_key, exit_code: 0, seconds: 42 });
    expect(d.phases[4]!.required[1]!.record).toBeNull();
    expect(d.phases[4]!.step_keys).toEqual([retro.idempotency_key]);
  });

  it("phase の無い工程は同じ実行ジョブの直前の phase.changed のフェーズに入り、5 つに無い名前は後ろの欄になる（I5・I7）", () => {
    const e = events();
    e.add("phase.changed", { phase: "実装", issues: [11] }, { job: "job-1" });
    e.add("phase.changed", { phase: "準備", issues: [] }, { job: "job-2" });
    const own = e.step("impl", {}, { job: "job-1" });
    const tagged = e.step("check", { phase: "検査" }, { job: "job-2" });
    const other = e.step("prep", {}, { job: "job-2" });
    const d = deriveDetail(runOf(), e.out, 0);
    const byKey = new Map(d.steps.map((s) => [s.key, s.phase]));
    expect([byKey.get(own.idempotency_key), byKey.get(tagged.idempotency_key), byKey.get(other.idempotency_key)]).toEqual(["実装", "検査", "準備"]);
    expect(d.phases.map((p) => p.name)).toEqual(["設計", "実装", "検査", "取り込み", "仕上げ", "準備"]);
    expect(d.current_phase).toBe("準備");
  });

  it("工程の費用は同じ job_id・step・at の usage.recorded の和で、別の at は足さず、無ければ null（I8）", () => {
    const e = events();
    const at = "2026-11-04T01:00:00.000Z";
    const s = e.step("impl", {}, { at });
    e.add("usage.recorded", { model: "opus", input_tokens: 10, output_tokens: 2, cost_usd: 0.5, step: "impl" }, { at });
    e.add("usage.recorded", { model: "haiku", input_tokens: 5, output_tokens: 1, cost_usd: 0.25, step: "impl" }, { at });
    e.add("usage.recorded", { model: "opus", input_tokens: 1, output_tokens: 1, cost_usd: 9, step: "impl" }, { at: "2026-11-04T02:00:00.000Z" });
    e.add("usage.recorded", { model: "opus", input_tokens: 1, output_tokens: 1, cost_usd: 1, step: "conductor" });
    const lonely = e.step("pr");
    const d = deriveDetail(runOf(), e.out, 0);
    const cost = new Map(d.steps.map((r) => [r.key, r.cost_usd]));
    expect(cost.get(s.idempotency_key)).toBe(0.75);
    expect(cost.get(lonely.idempotency_key)).toBeNull();
    expect(d.usage).toEqual({ input_tokens: 17, output_tokens: 5, cost_usd: 10.75 });
  });

  it("足す前の形の step.recorded だけの列でも詳細を導き、has_details は偽", () => {
    const e = events();
    e.step("impl");
    const d = deriveDetail(runOf(), e.out, 0);
    expect(d.steps[0]).toMatchObject({ phase: null, issues: [], has_details: false, cost_usd: null, pull_request: null });
    expect(d.columns.at(-1)!.step_keys).toHaveLength(1);
  });
});

describe("工程の中身（受け入れ条件 7）", () => {
  it("触ったファイルのリンク・差分のリンク・決めたこと・出力を返し、Pull Request があれば差分はその files", () => {
    const e = events();
    const range = { from: SHA("a"), to: SHA("b") };
    const s = e.step("impl", {
      files: [{ path: "factory/a b.ts", added: 3, deleted: 1 }],
      commit_range: range,
      decisions: [{ question: "どちら", answer: "A", by: "ai" }],
      output: { kind: "report", text: "## 作業の報告", truncated: false },
    });
    const { events: stripped } = capSteps(e.out, 2000);
    const row = stepRows(stripped)[0]!;
    expect(row.has_details).toBe(true);
    const detail = deriveStepDetail(runOf(), row, s);
    expect(detail.files).toEqual([{ path: "factory/a b.ts", added: 3, deleted: 1, url: `https://github.com/org-a/repo-a/blob/${SHA("b")}/factory/a%20b.ts` }]);
    expect(detail.diff_url).toBe(`https://github.com/org-a/repo-a/compare/${SHA("a")}...${SHA("b")}`);
    expect(detail.decisions).toEqual([{ question: "どちら", answer: "A", by: "ai" }]);
    expect(detail.output).toEqual({ kind: "report", text: "## 作業の報告", truncated: false });
    const withPr = e.step("pr", { pull_request: { repo: "org-a/repo-a", number: 7 } });
    expect(deriveStepDetail(runOf(), stepRows([withPr])[0]!, withPr).diff_url).toBe("https://github.com/org-a/repo-a/pull/7/files");
  });

  it("足した項目を持たない工程は、それぞれ null（空の配列にしない）", () => {
    const e = events();
    const s = e.step("impl");
    const detail = deriveStepDetail(runOf(), stepRows([s])[0]!, s);
    expect([detail.files, detail.files_omitted, detail.commit_range, detail.decisions, detail.output, detail.diff_url]).toEqual([null, null, null, null, null, null]);
  });
});

describe("詳細の上限（I11）", () => {
  it("step.recorded 2,001 件では最も古い 1 件を省き、ほかの種類はすべて残し、ゲートと使用量は全件から導いた値と同じ", () => {
    const e = events();
    e.add("gate.reached", { kind: "gate1", pr_url: null, material: "x" });
    e.add("usage.recorded", { model: "opus", input_tokens: 1, output_tokens: 1, cost_usd: 1, step: "conductor" });
    const first = e.step("impl");
    for (let i = 0; i < 2000; i += 1) e.step("impl");
    const capped = capSteps(e.out, 2000);
    expect(capped.omittedSteps).toBe(1);
    expect(capped.events.some((x) => x.idempotency_key === first.idempotency_key)).toBe(false);
    const d = deriveDetail(runOf(), capped.events, capped.omittedSteps);
    const whole = deriveDetail(runOf(), e.out, 0);
    expect(d.omitted_steps).toBe(1);
    expect(d.steps).toHaveLength(2000);
    expect(d.gates).toEqual(whole.gates);
    expect(d.usage).toEqual(whole.usage);
  });
});

describe("棚卸しの記録（受け入れ条件 15）", () => {
  it("実行をまたいで at の新しい順に並べる", () => {
    const a = events("sr-a");
    const old = a.step("refine", {}, { at: "2026-11-01T00:00:00.000Z" });
    const b = events("sr-b");
    const recent = b.step("refine", { pull_request: { repo: "org-a/repo-a", number: 3 } }, { at: "2026-11-03T00:00:00.000Z" });
    const list = deriveRefinements([
      { sprintRunId: "sr-a", steps: [old] },
      { sprintRunId: "sr-b", steps: [recent] },
    ]);
    expect(list.map((r) => r.sprint_run_id)).toEqual(["sr-b", "sr-a"]);
    expect(list[0]!.pull_request).toEqual({ repo: "org-a/repo-a", number: 3, url: "https://github.com/org-a/repo-a/pull/3" });
  });
});

describe("注意の行（#2430 の受け入れ条件 1〜3・I1・I2）", () => {
  it("attention は理由と要約を受けた値のまま、order_key の順にすべて行になる。base_conflict 以外の理由も落とさない", () => {
    const e = events();
    e.add("attention", { reason: "base_conflict", summary: "src/a.ts で衝突" }, { at: "2026-11-04T00:00:09.000Z" });
    e.step("impl");
    e.add("attention", { reason: "stopped", summary: "止まった" }, { at: "2026-11-04T00:00:01.000Z" });
    e.add("attention", { reason: "base_conflict", summary: "src/b.ts で衝突" });
    const rows = deriveDetail(runOf(), e.out, 0).attention;
    expect(rows.map((r) => [r.reason, r.summary])).toEqual([
      ["base_conflict", "src/a.ts で衝突"],
      ["stopped", "止まった"],
      ["base_conflict", "src/b.ts で衝突"],
    ]);
    expect(rows[0]).toEqual({ key: e.out[0]!.idempotency_key, at: "2026-11-04T00:00:09.000Z", reason: "base_conflict", summary: "src/a.ts で衝突" });
  });

  it("2,000 件を超える工程の記録と混ぜても attention は減らず、0 件なら空の配列", () => {
    const e = events();
    e.add("attention", { reason: "base_conflict", summary: "古い衝突" });
    for (let i = 0; i < 2001; i += 1) e.step("impl");
    e.add("attention", { reason: "base_conflict", summary: "新しい衝突" });
    const capped = capSteps(e.out, 2000);
    expect(attentionsOf(capped.events).map((r) => r.summary)).toEqual(["古い衝突", "新しい衝突"]);
    expect(deriveDetail(runOf(), events().out, 0).attention).toEqual([]);
  });
});

describe("承認を待つ依頼（#2430 の受け入れ条件 5・6・I3〜I5、決定 1）", () => {
  const waiting = (patch: Partial<SprintRun> = {}) => runOf({ state: "awaiting_approval", ...patch });

  it("最後の gate.reached を、種類・ループの参照・日時とともに 1 行にする。loop が無ければ null", () => {
    const e = events();
    e.add("gate.reached", { kind: "gate1", pr_url: null, material: "x", loop: { name: "lfactory2", lap: 3 } });
    const run = waiting({ sprintRunId: "sr-a" });
    expect(approvalRequestOf(run, awaitingGateOf(run, e.out))).toEqual({
      sprint_run_id: "sr-a",
      repository_id: 101,
      repo: "org-a/repo-a",
      issues: [11, 12],
      kind: "gate1",
      loop: { name: "lfactory2", lap: 3 },
      waiting_since: e.out[0]!.at,
    });
    const c = events();
    c.add("gate.reached", { kind: "decision", pr_url: null, material: "x" });
    expect(approvalRequestOf(run, awaitingGateOf(run, c.out))?.loop).toBeNull();
  });

  it("最後の gate.reached より後に同じ種類の gate.answered があれば行にしない。別の種類の答えでは外さない", () => {
    const e = events();
    e.add("gate.reached", { kind: "gate1", pr_url: null, material: "x" });
    e.add("gate.answered", { approval_request_id: "a1", kind: "gate1", decision: "approved", answered_by_account_id: "u" });
    expect(awaitingGateOf(waiting(), e.out)).toBeNull();
    const f = events();
    f.add("gate.reached", { kind: "gate2", pr_url: null, material: "x" });
    f.add("gate.answered", { approval_request_id: "a1", kind: "decision", answered_by_account_id: "u" });
    expect(awaitingGateOf(waiting(), f.out)?.payload.kind).toBe("gate2");
  });

  it("同じ種類の gate.reached が 2 度届くと 1 行で、日時は後のもの。答えた後に出し直した依頼は待ちに戻る", () => {
    const e = events();
    e.add("gate.reached", { kind: "gate1", pr_url: null, material: "x" });
    e.add("gate.answered", { approval_request_id: "a1", kind: "gate1", decision: "rejected", answered_by_account_id: "u" });
    e.add("gate.reached", { kind: "gate1", pr_url: null, material: "y" });
    const second = e.add("gate.reached", { kind: "gate1", pr_url: null, material: "z" });
    expect(awaitingGateOf(waiting(), e.out)?.at).toBe(second.at);
  });

  it("状態が awaiting_approval でない実行と、待つ理由が配布の依頼・止まった配布の実行は、答えが無くても行にしない", () => {
    const e = events();
    e.add("gate.reached", { kind: "gate1", pr_url: null, material: "x" });
    expect(awaitingGateOf(runOf({ state: "running" }), e.out)).toBeNull();
    expect(awaitingGateOf(waiting({ awaitingReason: "release_requested" }), e.out)).toBeNull();
    expect(awaitingGateOf(waiting({ awaitingReason: "release_stalled" }), e.out)).toBeNull();
    expect(awaitingGateOf(waiting(), [])).toBeNull();
  });

  it("行は待ち始めた日時の古い順で、同じ日時は実行の ID の順", () => {
    const row = (id: string, at: string) => ({ sprint_run_id: id, repository_id: 1, repo: "o/r", issues: [], kind: "gate1" as const, loop: null, waiting_since: at });
    const sorted = sortApprovalRequests([row("sr-c", "2026-11-04T00:00:02Z"), row("sr-b", "2026-11-04T00:00:01Z"), row("sr-a", "2026-11-04T00:00:02Z")]);
    expect(sorted.map((r) => r.sprint_run_id)).toEqual(["sr-b", "sr-a", "sr-c"]);
  });
});
