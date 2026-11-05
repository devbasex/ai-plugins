// Factory のオーガナイザーとの置き場の翻訳（#2439）。NDF の契約の例の組（parallel-external.json）を読み、写した事象が契約に合うかを縛る
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { validateProgressEvent, validateRunJobInstruction, type ProgressEvent, type RunJobInstruction } from "@ndf-factory/contracts";
import { runJob, type JobRuntime } from "./main.ts";
import { laneName, organizerEnv, releaseBodies, type OrganizerFiles } from "./organizer.ts";
import { common } from "./translate.ts";

const FORM = JSON.parse(readFileSync(new URL("../../../plugins/ndf/contracts/forms/parallel-external.json", import.meta.url), "utf8")) as {
  cases: { message: { file: string; body: Record<string, unknown> }; valid: boolean }[];
};

function memoryFiles(seed: Record<string, Record<string, unknown>> = {}): OrganizerFiles & { files: Map<string, unknown> } {
  const files = new Map<string, unknown>(Object.entries(seed));
  return {
    files,
    async write(rel, value) {
      files.set(rel, structuredClone(value));
    },
    async list(sub) {
      return [...files.entries()]
        .filter(([k]) => k.startsWith(`${sub}/`))
        .sort(([a], [b]) => (a < b ? -1 : 1))
        .map(([k, v]) => ({ name: k.slice(sub.length + 1), value: v as Record<string, unknown> }));
    },
  };
}

const ID = { sprintRunId: "sr1", jobId: "job1", repo: "org-a/repo-a" };
const full = (key: string, body: Pick<ProgressEvent, "type" | "payload">) =>
  ({ ...common(ID, "finished", "2026-11-02T00:00:00.000Z"), idempotency_key: key, ...body }) as ProgressEvent;

describe("置き場の翻訳（受け入れ条件 10、I59・I68）", () => {
  it("契約の例の組の正しいファイルは契約に合う事象へ写り、依頼の形が欠けたもの・知らない段は写らない", async () => {
    const valid = FORM.cases.filter((c) => c.valid && /^(requests|presented|progress|results)\//.test(c.message.file));
    expect(valid.length).toBeGreaterThanOrEqual(4);
    for (const c of valid) {
      const files = memoryFiles({ [c.message.file]: c.message.body });
      const bodies = await releaseBodies(files, { SPRINT_RUN_ID: "sr1", START: "release" });
      expect(bodies.length, c.message.file).toBeGreaterThan(0);
      for (const b of bodies) expect(validateProgressEvent(full(b.key, b.event)), `${c.message.file} ${b.event.type}`).toMatchObject({ ok: true });
    }
    const invalid = FORM.cases.filter((c) => !c.valid && /^(requests|progress)\//.test(c.message.file) && !c.message.file.startsWith("requests/loop-x"));
    for (const c of invalid) {
      expect(await releaseBodies(memoryFiles({ [c.message.file]: c.message.body }), { SPRINT_RUN_ID: "sr1", START: "release" })).toEqual([]);
    }
  });

  it("答えのある依頼は送り直さず、配布の実行の事象は配布の実行のジョブだけが送る", async () => {
    const req = FORM.cases.find((c) => c.valid && c.message.file.startsWith("requests/"))!.message;
    const res = FORM.cases.find((c) => c.valid && c.message.file.startsWith("results/"))!.message;
    const files = memoryFiles({ [req.file]: req.body, [res.file]: res.body });
    expect((await releaseBodies(files, { SPRINT_RUN_ID: "sr1", START: "loop" })).map((b) => b.event.type)).toEqual(["release.requested"]);
    await files.write(`answers/${req.body.id}.json`, { result: "released" });
    expect(await releaseBodies(files, { SPRINT_RUN_ID: "sr1", START: "loop" })).toEqual([]);
  });

  it("レーンの名前は #2436 のレーン名の形に収まり、NDF へ 4 つの環境変数を渡す", () => {
    const loop = laneName({ SPRINT_RUN_ID: "x", LOOP_NAME: "LFactory3-very-long-loop-name-over-limit" });
    const run = laneName({ SPRINT_RUN_ID: "0123456789abcdef0123456789abcdef" });
    for (const name of [loop, run]) expect(name).toMatch(/^[a-z0-9][a-z0-9_-]{0,31}$/);
    expect(run).toBe("run-0123456789abcdef");
    expect(loop).toBe("loop-lfactory3-very-long-loop-na");
    expect(organizerEnv({ SPRINT_RUN_ID: "sr1", NDF_VERSION: "10.28.0@0123abcd" }, "/w/repo/.ndf/organizer")).toEqual({
      NDF_FACTORY_RUN: "1",
      NDF_ORGANIZER_DIR: "/w/repo/.ndf/organizer",
      NDF_ORGANIZER_LANE: "run-sr1",
      NDF_VERSION: "10.28.0@0123abcd",
    });
  });
});

describe("配布の依頼と配布の実行のジョブ（受け入れ条件 7・10、I65）", () => {
  const base: RunJobInstruction = {
    SPRINT_RUN_ID: "sr1",
    JOB_ID: "job1",
    REPO: "org-a/repo-a",
    NDF_VERSION: "10.27.16",
    START: "new",
    ISSUES: "1",
    INGEST_URL: "https://api.example/ingest/events",
    INGEST_TOKEN_SECRET_ARN: "arn:aws:secretsmanager:us-east-1:1:secret:t",
    EXECUTION_MODE: "managed",
  };

  function harness(organizer: OrganizerFiles, conductor: JobRuntime["runConductor"]) {
    const events: ProgressEvent[] = [];
    const fetchImpl = (async (url: string, init: RequestInit = {}) => {
      if (url.endsWith("/ingest/events")) {
        events.push(JSON.parse(String(init.body)) as ProgressEvent);
        return new Response(null, { status: 202 });
      }
      if (url.endsWith("/run-jobs/organizer/answers")) return Response.json({ answers: [], ranges: [] });
      if (url.endsWith("/run-jobs/organizer/batch")) return Response.json({ seq: 1, step: "present", lanes: [], presentations: [], choices: ["continue", "reject"] });
      if (url.endsWith("/run-jobs/state") && String(init.body).includes('"get"')) return new Response(null, { status: 404 });
      return Response.json(url.endsWith("github-token") ? { token: "ghs" } : { url: "https://state.example/x" });
    }) as typeof fetch;
    const runtime: JobRuntime = {
      organizer,
      writeGitHubToken: async () => undefined,
      clone: async () => undefined,
      installNdf: async (v) => v,
      restoreState: async () => undefined,
      saveState: async () => undefined,
      readLoop: async () => null,
      answerLoop: vi.fn(async () => undefined),
      recordAnswer: vi.fn(async () => null),
      runConductor: conductor,
    };
    return { events, run: (inst: RunJobInstruction) => runJob({ instruction: inst, token: "tok", runtime, fetch: fetchImpl, setInterval: () => 1, clearInterval: () => undefined, stderr: () => undefined }) };
  }

  it("手動の実行が配布の依頼を出すと release.requested を依頼の id の鍵で送り、gate（release_requested）で終える", async () => {
    const req = FORM.cases.find((c) => c.valid && c.message.file.startsWith("requests/"))!.message;
    const files = memoryFiles();
    const t = harness(files, async () => {
      await files.write(req.file, req.body);
      return { report: "判断が要る", summary: "配布待ち", gate: null };
    });
    expect(await t.run(base)).toBe(0);
    const requested = t.events.find((e) => e.type === "release.requested")!;
    expect(requested.idempotency_key).toBe(req.body.id);
    expect(validateProgressEvent(requested).ok).toBe(true);
    expect(t.events.some((e) => e.type === "gate.reached")).toBe(false);
    expect(t.events.at(-1)).toMatchObject({ type: "job.finished", payload: { result: "gate", reason: "release_requested" } });
    expect(validateProgressEvent(t.events.at(-1)!).ok).toBe(true);
  });

  it("配布の実行はまとめた配布の中身を置き場へ書き、承認資料で止まれば提示と gate2 を送る", async () => {
    const files = memoryFiles();
    const inst = { ...base, START: "release" as const, ISSUES: undefined, RELEASE_SEQ: "1", RELEASE_STEP: "present" as const };
    expect(validateRunJobInstruction(Object.fromEntries(Object.entries(inst).filter(([, v]) => v !== undefined))).ok).toBe(true);
    const presented = FORM.cases.find((c) => c.valid && c.message.file.startsWith("presented/"))!.message;
    const t = harness(files, async () => {
      expect(files.files.get("batch.json")).toMatchObject({ seq: 1, step: "present" });
      await files.write(presented.file, presented.body);
      return { report: "判断が要る", summary: "承認ゲート 2", gate: null };
    });
    await t.run(inst);
    expect(t.events.map((e) => e.type)).toEqual(["job.started", "release.presented", "gate.reached", "job.finished"]);
    expect(t.events.find((e) => e.type === "release.presented")!.idempotency_key).toBe("sr1:presented:1");
    expect(t.events.find((e) => e.type === "gate.reached")!.payload).toMatchObject({ kind: "gate2" });
    for (const e of t.events) expect(validateProgressEvent(e).ok, e.type).toBe(true);
  });

  it("配布の実行が提示を書かずに本番のプランの中で止まれば、conductor が gate2 と言っても判断（decision）で送る", async () => {
    const files = memoryFiles();
    const inst = { ...base, START: "release" as const, ISSUES: undefined, RELEASE_SEQ: "1", RELEASE_STEP: "confirm:1" as const };
    const t = harness(files, async () => ({ report: "判断が要る", summary: "本番のプランが止まった", gate: { kind: "gate2", pr_url: null, material: "資料" } }));
    await t.run(inst);
    expect(t.events.find((e) => e.type === "gate.reached")!.payload).toMatchObject({ kind: "decision" });
  });
});
