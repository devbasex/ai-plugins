// #2347: 区間・ndf-next の読み方・ループの記録の翻訳・同時のジョブの数の目安・START=loop の手順
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { validateProgressEvent, type ProgressEvent, type RunJobInstruction } from "@ndf-factory/contracts";
import { checkMaxJobs, capacityHint } from "./capacity.ts";
import { advancedSince, answersToWrite, lapClosedEvents, loopStoppedEvent, markOf, parseLoopFile, type LoopFile } from "./loop-file.ts";
import { runJob, type ConductorOutcome, type JobRuntime } from "./main.ts";
import { readNext } from "./next-block.ts";
import { firstPrompt } from "./runtime.ts";
import { limitResetAt, runSections, sectionLimitOf, type SectionRun } from "./sections.ts";
import { common, translateProgressLine } from "./translate.ts";

const FORM = JSON.parse(readFileSync(new URL("../../../plugins/ndf/contracts/forms/ndf-next-block.json", import.meta.url), "utf8")) as {
  cases: { text: string; blocks: string[]; launch: boolean }[];
};

describe("ndf-next の読み方（受け入れ条件 15・17、I55）", () => {
  it.each(FORM.cases)("NDF の契約の例の組と同じ答えを返す: %#", (c) => {
    const got = readNext(c.text);
    expect(got.blocks).toEqual(c.blocks);
    expect(got.launch).toBe(c.launch);
  });
});

const ok = (text: string): SectionRun => ({ code: 0, text, result: { type: "result", result: text, is_error: false } });
const block = (cmd: string) => `- 結果: 完了\n\n\`\`\`ndf-next\n${cmd}\n\`\`\`\n`;

describe("区間（受け入れ条件 15・16、I51）", () => {
  it("ndf-next が 1 つなら次の区間を起動し、区間ごとに番号と指示を知らせる。無ければ終える", async () => {
    const replies = [ok(block("/ndf:development-workflow #2")), ok("- 結果: 完了\n")];
    const launched: string[] = [];
    const started: [number, string][] = [];
    const out = await runSections({
      firstPrompt: "/ndf:development-workflow #1",
      limit: 20,
      detectUsageLimit: false,
      launch: async (p) => (launched.push(p), replies.shift()!),
      started: async (n, c) => void started.push([n, c]),
    });
    expect(launched).toEqual(["/ndf:development-workflow #1", "/ndf:development-workflow #2"]);
    expect(started).toEqual([
      [1, "/ndf:development-workflow #1"],
      [2, "/ndf:development-workflow #2"],
    ]);
    expect(out).toMatchObject({ end: { kind: "finished" }, sections: 2 });
  });

  it.each([
    ["判断が要る", `- 結果: 判断が要る\n\n\`\`\`ndf-next\n/x\n\`\`\`\n`],
    ["ブロックが 2 つ", `${block("/a")}\n${block("/b")}`],
  ])("%s なら次の区間を起動しない", async (_name, text) => {
    const launch = vi.fn(async () => ok(text));
    const out = await runSections({ firstPrompt: "/p", limit: 20, detectUsageLimit: false, launch, started: async () => undefined });
    expect(launch).toHaveBeenCalledTimes(1);
    expect(out.end.kind).toBe("finished");
  });

  it("区間の上限の回数の後は起動せず section_limit で終える（最初の起動も数える）", async () => {
    const launch = vi.fn(async () => ok(block("/again")));
    const out = await runSections({ firstPrompt: "/p", limit: sectionLimitOf({}), detectUsageLimit: false, launch, started: async () => undefined });
    expect(launch).toHaveBeenCalledTimes(20);
    expect(out).toMatchObject({ end: { kind: "section_limit" }, sections: 20 });
    expect(sectionLimitOf({ RUNNER_SECTION_LIMIT: "3" })).toBe(3);
    expect(sectionLimitOf({ RUNNER_SECTION_LIMIT: "0" })).toBe(20);
  });

  it("ループの周回が締まった区間の後は ndf-next があっても起動しない（決定 5）", async () => {
    const launch = vi.fn(async () => ok(block("/next-lap")));
    const out = await runSections({
      firstPrompt: "/p",
      limit: 20,
      detectUsageLimit: false,
      launch,
      started: async () => undefined,
      lapClosed: async () => true,
    });
    expect(launch).toHaveBeenCalledTimes(1);
    expect(out.end.kind).toBe("lap_closed");
  });

  it("local だけが利用上限を読み、リセット時刻を返す（受け入れ条件 18・19、I54）", async () => {
    const limited: SectionRun = { code: 1, text: "Claude AI usage limit reached|1793000000", result: { type: "result", is_error: true } };
    const local = await runSections({ firstPrompt: "/p", limit: 20, detectUsageLimit: true, launch: async () => limited, started: async () => undefined });
    expect(local.end).toEqual({ kind: "usage_limit", resetsAt: new Date(1793000000 * 1000).toISOString() });
    const managed = await runSections({ firstPrompt: "/p", limit: 20, detectUsageLimit: false, launch: async () => limited, started: async () => undefined });
    expect(managed.end.kind).toBe("finished");
  });

  it("リセット時刻は時間帯つきの時刻も読み、読めなければ null", () => {
    const now = new Date("2026-11-02T03:00:00Z"); // 東京の 12:00
    expect(limitResetAt("You've hit your limit · resets 3pm (Asia/Tokyo)", now)).toBe("2026-11-02T06:00:00.000Z");
    expect(limitResetAt("resets 9am (Asia/Tokyo)", now)).toBe("2026-11-03T00:00:00.000Z");
    expect(limitResetAt("usage limit", now)).toBeNull();
  });
});

const LOOP: LoopFile = parseLoopFile({
  name: "lfactory2",
  laps: [
    { n: 1, outcome: "完了", cost: 1.5, seconds: 60, versions: { dev: "1.0.0-dev.1" }, selection: [{ number: 11, decision: "選んだ" }] },
  ],
  waiting: [
    { number: 21, reason: "MVV の外の疑い", lap: 1 },
    { number: 22, reason: "答えた", lap: 1, answer: "approved" },
  ],
  stop: null,
})!;

describe("ループの記録の翻訳（受け入れ条件 2・12）", () => {
  it("始めの印より後に締まった周回を loop.lap_closed にし、答えの無い人の判断待ちを載せる", () => {
    const mark = markOf(parseLoopFile({ name: "lfactory2", laps: [], waiting: [], stop: null }));
    const events = lapClosedEvents(mark, LOOP);
    expect(events).toEqual([
      {
        type: "loop.lap_closed",
        payload: {
          loop: "lfactory2",
          lap: 1,
          issues: [11],
          result: "完了",
          version: "1.0.0-dev.1",
          cost_usd: 1.5,
          seconds: 60,
          pending: [{ number: 21, reason: "MVV の外の疑い", lap: 1 }],
        },
      },
    ]);
    expect(advancedSince(mark, LOOP)).toBe(true);
    expect(advancedSince(markOf(LOOP), LOOP)).toBe(false);
  });

  it("前の周回から残る答えの無い判断待ちも、後の周回の loop.lap_closed に載せる", () => {
    const two = parseLoopFile({
      name: "lfactory2",
      laps: [...LOOP.laps, { n: 2, outcome: "完了", cost: 0, seconds: 0, selection: [] }],
      waiting: [...LOOP.waiting, { number: 23, reason: "次の周回", lap: 2 }],
      stop: null,
    });
    const [event] = lapClosedEvents(markOf(LOOP), two);
    expect(event!.payload.pending.map((p) => p.number)).toEqual([21, 23]);
  });

  it("新しく書かれた止まる条件だけを loop.stopped にする。知らない条件は送らない", () => {
    const stopped = { ...LOOP, stop: { condition: "budget_usd", values: { usd: 60 }, lap: 1, at: "2026-11-02T00:00:00Z" } };
    expect(loopStoppedEvent(markOf(LOOP), stopped)).toMatchObject({ type: "loop.stopped", payload: { condition: "budget_usd", loop: "lfactory2" } });
    expect(loopStoppedEvent(markOf(stopped), stopped)).toBeNull();
    expect(loopStoppedEvent(markOf(LOOP), { ...stopped, stop: { ...stopped.stop, condition: "other" } })).toBeNull();
  });

  it("答えは、人の判断待ちにまだ答えの無い番号だけ書く（同じ答えを 2 回打たない）", () => {
    expect(answersToWrite("21=approved#40,22=rejected,99=approved", LOOP)).toEqual([{ number: 21, decision: "approved", pr: 40 }]);
  });
});

describe("同時のジョブの数の目安（受け入れ条件 10）", () => {
  it("8 コア・30 GiB で 6、2 コア・4 GiB で 1。目安を超える申告は警告し、申告は変えない", () => {
    expect(capacityHint(8, 30 * 1024 ** 3)).toBe(6);
    expect(capacityHint(2, 4 * 1024 ** 3)).toBe(1);
    const lines: string[] = [];
    expect(checkMaxJobs(4, 1, (l) => lines.push(l))).toBe(4);
    expect(lines).toHaveLength(1);
    expect(checkMaxJobs(undefined, 1, (l) => lines.push(l))).toBe(1);
    expect(lines).toHaveLength(1);
  });
});

describe("取り込みの衝突（受け入れ条件 7、決定 10）", () => {
  it("step が base-sync の attention だけを base_conflict にする", () => {
    expect(translateProgressLine({ kind: "attention", step: "base-sync", reason: "stopped", text: "衝突" }, "o/r")).toEqual({
      type: "attention",
      payload: { reason: "base_conflict", summary: "衝突" },
    });
    expect(translateProgressLine({ kind: "attention", step: "impl", reason: "stopped", text: "x" }, "o/r")?.payload).toMatchObject({ reason: "stopped" });
  });
});

describe("START=loop の手順（受け入れ条件 1・12・13・18）", () => {
  const base: RunJobInstruction = {
    SPRINT_RUN_ID: "sr1",
    JOB_ID: "job1",
    REPO: "org-a/repo-a",
    NDF_VERSION: "10.26.1",
    START: "loop",
    LOOP_NAME: "lfactory2",
    LOOP_MILESTONES: "25 Factory,26 根本",
    LOOP_BUDGET: "laps=3,usd=60",
    LOOP_ANSWERS: "21=approved#40,22=rejected",
    INGEST_URL: "https://api.example/ingest/events",
    INGEST_TOKEN_SECRET_ARN: "arn:aws:secretsmanager:us-east-1:1:secret:t",
    EXECUTION_MODE: "managed",
  };

  function harness(outcome: ConductorOutcome, after: LoopFile | null, opts: { stateStatus?: number } = {}) {
    const events: ProgressEvent[] = [];
    const fetchImpl = (async (url: string, init: RequestInit) => {
      if (url.endsWith("/ingest/events")) {
        events.push(JSON.parse(String(init.body)) as ProgressEvent);
        return new Response(null, { status: 202 });
      }
      if (url.endsWith("/run-jobs/state") && String(init.body).includes('"get"') && opts.stateStatus) {
        return new Response(null, { status: opts.stateStatus });
      }
      return Response.json(url.endsWith("github-token") ? { token: "ghs_1" } : { url: "https://state.example/x" });
    }) as typeof fetch;
    let reads = 0;
    const runtime: JobRuntime = {
      writeGitHubToken: async () => undefined,
      clone: async () => undefined,
      installNdf: async () => "10.26.1",
      restoreState: vi.fn(async () => undefined),
      saveState: vi.fn(async () => undefined),
      // 始めの 2 回（答えの前・印）は締めた周回の無い記録、終わりは after
      readLoop: async () => (++reads <= 2 ? { ...LOOP, laps: [] } : after),
      answerLoop: vi.fn(async () => undefined),
      recordAnswer: vi.fn(async () => null),
      runConductor: vi.fn(async () => outcome),
    };
    const run = () =>
      runJob({ instruction: base, token: "tok", runtime, fetch: fetchImpl, setInterval: () => 1, clearInterval: () => undefined, stderr: () => undefined });
    return { events, runtime, run };
  }

  it("最初の周回は状態が無くても始め、答えの無い番号だけ答えを書き、締めた周回を job.finished より前に送って done で終える", async () => {
    const t = harness({ report: null, summary: "", gate: null, end: { kind: "lap_closed" } }, LOOP, { stateStatus: 404 });
    expect(await t.run()).toBe(0);
    expect(t.runtime.restoreState).not.toHaveBeenCalled();
    expect(t.runtime.answerLoop).toHaveBeenCalledTimes(1);
    expect(t.runtime.answerLoop).toHaveBeenCalledWith("lfactory2", { number: 21, decision: "approved", pr: 40 });
    expect(t.events.map((e) => e.type)).toEqual(["job.started", "loop.lap_closed", "job.finished"]);
    expect(t.events[0]!.payload).toMatchObject({ start: "loop" });
    expect(t.events.at(-1)!.payload).toEqual({ result: "done", summary: "" });
    for (const e of t.events) expect(validateProgressEvent(e).ok).toBe(true);
  });

  it("判断が要るで止まったループの実行の gate.reached はループの名前と周回の番号を持つ", async () => {
    const t = harness({ report: "判断が要る", summary: "s", gate: { kind: "gate2", pr_url: null, material: "資料" }, end: { kind: "finished" } }, { ...LOOP, laps: [{ n: 2, outcome: "進行中" }] });
    await t.run();
    const gate = t.events.find((e) => e.type === "gate.reached")!;
    expect(gate.payload).toMatchObject({ kind: "gate2", loop: { name: "lfactory2", lap: 2 } });
    expect(t.events.at(-1)!.payload).toMatchObject({ result: "gate" });
  });

  it("利用上限は stopped・usage_limit・リセット時刻、区間の上限は stopped・section_limit で終える", async () => {
    const limited = harness({ report: null, summary: "", gate: null, end: { kind: "usage_limit", resetsAt: "2026-11-02T06:00:00.000Z" } }, null);
    await limited.run();
    expect(limited.events.at(-1)!.payload).toEqual({ result: "stopped", summary: "", reason: "usage_limit", resets_at: "2026-11-02T06:00:00.000Z" });
    expect(limited.runtime.saveState).toHaveBeenCalled();
    const capped = harness({ report: null, summary: "", gate: null, end: { kind: "section_limit" } }, null);
    await capped.run();
    expect(capped.events.at(-1)!.payload).toEqual({ result: "stopped", summary: "", reason: "section_limit" });
    expect(validateProgressEvent(capped.events.at(-1)).ok).toBe(true);
  });

  it("承認の依頼の識別子の無い resume は答えを書かずに続ける（決定 7）", async () => {
    const t = harness({ report: "完了", summary: "", gate: null, end: { kind: "finished" } }, null);
    const { LOOP_MILESTONES: _m, LOOP_BUDGET: _b, LOOP_ANSWERS: _a, ...rest } = base;
    await runJob({
      instruction: { ...rest, START: "resume" },
      token: "tok",
      runtime: t.runtime,
      fetch: (async (url: string, init: RequestInit) => {
        if (url.endsWith("/ingest/events")) return new Response(null, { status: 202 });
        return Response.json({ url: "https://state.example/x", token: "t", body: init.body });
      }) as typeof fetch,
      setInterval: () => 1,
      clearInterval: () => undefined,
      stderr: () => undefined,
    });
    expect(t.runtime.recordAnswer).not.toHaveBeenCalled();
    expect(t.runtime.restoreState).toHaveBeenCalled();
  });

  it("周回の conductor は --loop・--milestones・宣言した予算で起動する", () => {
    expect(firstPrompt(base, null)).toBe('/ndf:development-workflow --loop lfactory2 --milestones "25 Factory,26 根本" --budget-laps 3 --budget-usd 60');
  });

  it("job.finished の冪等の鍵は今の形のまま", () => {
    expect(common({ sprintRunId: "sr1", jobId: "job1", repo: "o/r" }, "finished", "2026-11-02T00:00:00.000Z").idempotency_key).toBe("job1:finished");
  });
});
