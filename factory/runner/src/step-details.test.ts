import { mkdtempSync, mkdirSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { validateProgressEvent, type ProgressEvent, type StepRecordedEvent } from "@ndf-factory/contracts";
import { redact } from "./redact.ts";
import { collectStepDetails, parseNumstat, type GitExec } from "./step-details.ts";
import { common, fitStep, MAX_EVENT_BYTES, phaseChanged, phaseNameOf, translateProgressLine } from "./translate.ts";

const ID = { sprintRunId: "sr1", jobId: "job1", repo: "org-a/repo-a" };
const AT = "2026-11-04T09:02:11.000Z";
const full = (body: Pick<ProgressEvent, "type" | "payload">) => ({ ...common(ID, 1, AT), ...body }) as ProgressEvent;
const SHA = (c: string) => c.repeat(40);

describe("フェーズの名前の写し（受け入れ条件 4・決定 1）", () => {
  it("プランの フェーズ の 6 つの値を 5 つの名前へ写し、表に無い値・読めないときは計画の名前を送る", () => {
    const cases: [string, string][] = [
      ["設計", "設計"],
      ["実装", "実装"],
      ["検査", "検査"],
      ["配布（開発版）", "取り込み"],
      ["配布（本番）", "仕上げ"],
      ["まとめ", "仕上げ"],
    ];
    for (const [plan, name] of cases) expect(phaseNameOf("7-release", plan)).toBe(name);
    expect(phaseNameOf("8-release-request", "配布の依頼")).toBe("release-request");
    expect(phaseNameOf("5-impl-2172", null)).toBe("impl");
    const changed = phaseChanged("5-impl-2172", "実装");
    expect(changed.payload).toEqual({ phase: "実装", issues: [2172] });
    expect(validateProgressEvent(full(changed)).ok).toBe(true);
  });
});

describe("伏せ字（I3・決定 7）", () => {
  it("NDF の 4 つの形と、ランナーが持つ秘密の値を *** にする", () => {
    const text = [
      "push https://x-access-token:abcdEFGH1234@github.com/o/r.git",
      "token ghs_ABCDEFGHijklmnop1234",
      "x-access-token:zzzzzzzzzz に失敗",
      "Authorization: Bearer eyJhbGciOi.secret",
      "key sk-ant-test-SECRET-0001 を読んだ",
    ].join("\n");
    const out = redact(text, ["sk-ant-test-SECRET-0001", "short"]);
    for (const leaked of ["abcdEFGH1234", "ghs_ABCDEFGHijklmnop1234", "zzzzzzzzzz", "eyJhbGciOi.secret", "sk-ant-test-SECRET-0001"]) {
      expect(out).not.toContain(leaked);
    }
    expect(out).toContain("https://***@github.com/o/r.git");
    expect(out).toContain("Authorization: ***");
  });
});

describe("事象 1 件の上限（I2）", () => {
  // 4 バイトの文字で、定義の上限の内のまま 300 KiB を超える
  it("300 KiB になる工程の中身を 256 KiB 以下に削り、output.truncated を真にし、契約に合う", () => {
    const payload: StepRecordedEvent["payload"] = {
      step: "impl",
      exit_code: 0,
      seconds: 1,
      summary: "s",
      output: { kind: "report", text: "あ".repeat(10_000), truncated: false },
      files: Array.from({ length: 100 }, (_, i) => ({ path: `${"𠀋".repeat(500)}${i}`, added: 1, deleted: 0 })),
      decisions: Array.from({ length: 20 }, () => ({ question: "𠀋".repeat(1000), answer: "𠀋".repeat(1000), by: "ai" as const })),
    };
    expect(Buffer.byteLength(JSON.stringify(payload))).toBeGreaterThan(300 * 1024);
    const fitted = fitStep(payload);
    const event = full({ type: "step.recorded", payload: fitted });
    expect(Buffer.byteLength(JSON.stringify(event))).toBeLessThanOrEqual(MAX_EVENT_BYTES);
    expect(fitted.output!.truncated).toBe(true);
    expect(validateProgressEvent(event).ok).toBe(true);
  });

  it("上限に収まる工程はそのまま送る", () => {
    const payload: StepRecordedEvent["payload"] = { step: "impl", exit_code: 0, seconds: 1, summary: "s", files: [] };
    expect(fitStep(payload)).toEqual(payload);
  });
});

describe("工程の中身の収集（決定 3〜6）", () => {
  const setup = () => {
    const root = mkdtempSync(join(tmpdir(), "step-details-"));
    const workdir = join(root, "wt");
    mkdirSync(workdir);
    const stateDir = join(root, "5-impl-2172-state");
    mkdirSync(stateDir);
    writeFileSync(join(root, "5-impl-2172.json"), JSON.stringify({ フェーズ: "実装", 作業場所: workdir }));
    writeFileSync(join(stateDir, "01-impl.out"), "## 作業の報告\n- 結果: 完了\ntoken ghs_ABCDEFGHijklmnop1234\n");
    return { root, workdir, stateDir };
  };
  const line = { kind: "step", at: AT, step: "impl", type: "work", exit: 0, seconds: 60, summary: "済んだ", out: "01-impl.out" };

  it("プランのフェーズ・計画の名前の課題・時間の窓のコミットのファイル・作業場所の判断・作業の報告を集め、伏せて送る", async () => {
    const { workdir, stateDir } = setup();
    const calls: { args: string[]; cwd: string }[] = [];
    const git: GitExec = async (args, cwd) => {
      calls.push({ args, cwd });
      return { code: 0, stdout: [`@${SHA("b")} ${SHA("a")}`, "", "3\t1\tfactory/a.ts", "-\t-\timg.png", `@${SHA("a")} ${SHA("9")}`, "", "2\t0\tfactory/a.ts", ""].join("\n") };
    };
    const ledgerRows = async () => [
      { kind: "decision", cwd: `${workdir}/sub`, at: "2026-11-04T09:01:40Z", question: "どちらにする", answer: "A にする" },
      { kind: "decision", cwd: "/elsewhere", at: "2026-11-04T09:01:40Z", question: "別の作業場所", answer: "x" },
      { kind: "decision", cwd: workdir, at: "2026-11-04T08:00:00Z", question: "窓の外", answer: "x" },
    ];
    const details = await collectStepDetails({ git, ledgerRows, secrets: async () => [] }, "5-impl-2172", stateDir, line);
    expect(calls[0]!.cwd).toBe(workdir);
    expect(calls[0]!.args).toContain("--since=2026-11-04T09:01:11+00:00");
    expect(calls[0]!.args).toContain("--until=2026-11-04T09:02:11+00:00");
    expect(details).toMatchObject({
      phase: "実装",
      issues: [2172],
      files: [
        { path: "factory/a.ts", added: 5, deleted: 1 },
        { path: "img.png", added: null, deleted: null },
      ],
      commit_range: { from: SHA("9"), to: SHA("b") },
      decisions: [{ question: "どちらにする", answer: "A にする", by: "ai" }],
      output: { kind: "report", truncated: false },
    });
    expect(details.output!.text).not.toContain("ghs_ABCDEFGHijklmnop1234");
    const body = translateProgressLine(line, ID.repo, details)!;
    expect(validateProgressEvent(full(body)).ok).toBe(true);
  });

  it("作業場所が無い・git が 0 以外で終わると、ファイルとコミットの範囲を省く。run のステップはログ", async () => {
    const { stateDir } = setup();
    const git: GitExec = async () => ({ code: 128, stdout: "" });
    const details = await collectStepDetails({ git, ledgerRows: async () => [], secrets: async () => [] }, "5-impl-2172", stateDir, { ...line, type: "run" });
    expect(details.files).toBeUndefined();
    expect(details.commit_range).toBeUndefined();
    expect(details.output!.kind).toBe("log");
  });

  it("プランの JSON が読めないときはフェーズに計画の名前を入れ、スプリントで 1 つの計画は課題を持たない", async () => {
    const root = mkdtempSync(join(tmpdir(), "step-details-"));
    const stateDir = join(root, "6-check-state");
    mkdirSync(stateDir);
    const details = await collectStepDetails({ git: async () => ({ code: 0, stdout: "" }), ledgerRows: async () => [], secrets: async () => [] }, "6-check", stateDir, line);
    expect(details).toEqual({ phase: "check" });
  });

  it("出力の参照が状態のディレクトリの外（../・絶対パス・外を指すシンボリックリンク）なら出力を省く", async () => {
    const root = mkdtempSync(join(tmpdir(), "step-details-"));
    const stateDir = join(root, "6-check-state");
    mkdirSync(stateDir);
    writeFileSync(join(root, "secret.pem"), "PRIVATE KEY");
    symlinkSync(join(root, "secret.pem"), join(stateDir, "01-link.out"));
    writeFileSync(join(stateDir, "02-check.out"), "通った");
    const deps = { git: async () => ({ code: 0, stdout: "" }), ledgerRows: async () => [], secrets: async () => [] };
    for (const out of ["../secret.pem", join(root, "secret.pem"), "01-link.out"]) {
      const details = await collectStepDetails(deps, "6-check", stateDir, { ...line, out });
      expect(details.output).toBeUndefined();
    }
    const inside = await collectStepDetails(deps, "6-check", stateDir, { ...line, out: "02-check.out" });
    expect(inside.output!.text).toBe("通った");
  });

  it("コミットの無い工程は空の配列で、コミットの範囲を持たない", () => {
    expect(parseNumstat("")).toEqual({ files: [], files_omitted: 0 });
  });
});
