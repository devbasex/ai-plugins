// ランナーの起動（F9）。実行ジョブの指示を環境変数から読み、取り込み・NDF の導入と版の固定・状態の復元・答えの記録・
// conductor の起動（ndf-next の区間をつなぐ）・翻訳と送信・状態の保存・終わりの事象を順に行う。終了コードは 0（job.finished を送った）か 1 だけ（I43）
import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";
import { validateRunJobInstruction, type GateReachedEvent, type JobFinishedEvent, type ProgressEvent, type RunJobInstruction } from "@ndf-factory/contracts";
import { advancedSince, answersToWrite, currentLapOf, lapClosedEvents, loopStoppedEvent, markOf, type LoopFile } from "./loop-file.ts";
import type { SectionEnd } from "./sections.ts";
import { createSender, type SendResult } from "./send.ts";
import { common, finishedResult, truncateGate, type JobIdentity, type ReportResult } from "./translate.ts";

// 導入の token を取り直す間隔（GitHub の導入の token は 1 時間で切れる。決定 7）
export const GITHUB_TOKEN_REFRESH_MS = 50 * 60 * 1000;

// ジョブの中身。テストは偽物に替え、本物は runtime.ts が持つ
export interface JobRuntime {
  // 導入の token を gh の設定ファイルへ書く
  writeGitHubToken(token: string): Promise<void>;
  clone(repo: string): Promise<void>;
  // NDF を入れ、入った版を返す
  installNdf(version: string): Promise<string>;
  restoreState(url: string): Promise<void>;
  saveState(url: string): Promise<void>;
  // 開発ループの記録（.ndf/loop/<名>/loop.json）を読む。無ければ null
  readLoop(name: string): Promise<LoopFile | null>;
  // 人の判断待ちへの答えを NDF の loop answer で書く
  answerLoop(name: string, answer: { number: number; decision: "approved" | "rejected"; pr: number | null }): Promise<void>;
  // 承認の依頼の答えを NDF へ記録する（ゲートは sprint-state.py gate --by user、判断は conductor への指示を返す）
  recordAnswer(approvalRequestId: string): Promise<string | null>;
  // conductor を動かし（ndf-next の区間をつなぐ）、伸びた分の事象を emit へ渡す。最後の作業の報告の結果と承認資料、区間の終わり方を返す。
  // lapClosed はループの実行で区間ごとに呼び、真なら次の区間を起動しない（決定 5）
  runConductor(
    instruction: RunJobInstruction,
    directive: string | null,
    emit: (body: Pick<ProgressEvent, "type" | "payload">) => Promise<void>,
    lapClosed?: () => Promise<boolean>,
  ): Promise<ConductorOutcome>;
}

export interface ConductorOutcome {
  report: ReportResult;
  gate: Pick<GateReachedEvent["payload"], "kind" | "pr_url" | "material"> | null;
  summary: string;
  end?: SectionEnd;
}

export interface RunnerDeps {
  instruction: RunJobInstruction;
  token: string;
  runtime: JobRuntime;
  fetch?: typeof fetch;
  now?: () => Date;
  setInterval?: (fn: () => void, ms: number) => { unref?: () => void } | number;
  clearInterval?: (handle: unknown) => void;
  sleep?: (ms: number) => Promise<void>;
  stderr?: (line: string) => void;
}

const apiUrl = (ingestUrl: string, path: string) => new URL(path, new URL(ingestUrl).origin).toString();

export async function runJob(deps: RunnerDeps): Promise<0 | 1> {
  const { instruction, token, runtime } = deps;
  const http = deps.fetch ?? fetch;
  const now = deps.now ?? (() => new Date());
  const stderr = deps.stderr ?? ((line) => process.stderr.write(`${line}\n`));
  const id: JobIdentity = { sprintRunId: instruction.SPRINT_RUN_ID, jobId: instruction.JOB_ID, repo: instruction.REPO };
  const send = createSender({ url: instruction.INGEST_URL, token: () => token, fetch: http, sleep: deps.sleep, stderr });
  let seq = 0;
  const emit = async (body: Pick<ProgressEvent, "type" | "payload">): Promise<SendResult> =>
    send({ ...common(id, ++seq, now().toISOString()), ...body } as ProgressEvent);

  const callApi = async (path: string, body: unknown = {}, missingOk = false) => {
    const res = await http(apiUrl(instruction.INGEST_URL, path), {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(body),
    });
    if (missingOk && res.status === 404) return null;
    if (!res.ok) throw new Error(`${path} が ${res.status} を返した`);
    return (await res.json()) as Record<string, string>;
  };

  const refreshGitHubToken = async () => runtime.writeGitHubToken((await callApi("/run-jobs/github-token"))!.token!);

  let failed = false;
  let summary = "";
  let gate: GateReachedEvent["payload"] | null = null;
  let report: ReportResult = null;
  let end: SectionEnd | undefined;
  let lapAdvanced = false;
  const loopName = instruction.LOOP_NAME ?? null;
  const timer = (deps.setInterval ?? setInterval)(() => {
    refreshGitHubToken().catch((err: Error) => stderr(`導入の token を取り直せない: ${err.message}`));
  }, GITHUB_TOKEN_REFRESH_MS);
  try {
    await refreshGitHubToken();
    await runtime.clone(instruction.REPO);
    const installed = await runtime.installNdf(instruction.NDF_VERSION);
    if (installed !== instruction.NDF_VERSION) throw new Error(`入った NDF の版が違う（${installed}）`);
    await emit({ type: "job.started", payload: { ndf_version: instruction.NDF_VERSION, start: instruction.START } });
    let directive: string | null = null;
    if (instruction.START === "resume" || instruction.START === "loop") {
      // ループの最初の周回は保存した状態が無い（404）。空から始める
      const state = await callApi("/run-jobs/state", { op: "get" }, instruction.START === "loop");
      if (state) await runtime.restoreState(state.url!);
      // 承認の依頼の識別子の無い resume（利用上限の後）は答えを書かずに続ける（決定 7）
      if (instruction.START === "resume" && instruction.APPROVAL_REQUEST_ID) directive = await runtime.recordAnswer(instruction.APPROVAL_REQUEST_ID);
    }
    let lapClosed: (() => Promise<boolean>) | undefined;
    let mark = markOf(null);
    if (loopName) {
      for (const answer of answersToWrite(instruction.LOOP_ANSWERS, await runtime.readLoop(loopName))) await runtime.answerLoop(loopName, answer);
      mark = markOf(await runtime.readLoop(loopName));
      lapClosed = async () => advancedSince(mark, await runtime.readLoop(loopName));
    }
    const out = await runtime.runConductor(
      instruction,
      directive,
      async (body) => {
        await emit(body);
      },
      lapClosed,
    );
    report = out.report;
    summary = out.summary;
    end = out.end;
    let loopAfter: LoopFile | null = null;
    if (loopName) {
      // loop.* は job.finished より前に送る（実行の制御は停止の後に同じジョブの事象として読む）
      loopAfter = await runtime.readLoop(loopName);
      for (const body of lapClosedEvents(mark, loopAfter)) await emit(body);
      const stopped = loopStoppedEvent(mark, loopAfter);
      if (stopped) await emit(stopped);
      lapAdvanced = advancedSince(mark, loopAfter);
    }
    await runtime.saveState((await callApi("/run-jobs/state", { op: "put" }))!.url!);
    if (report === "判断が要る" && end?.kind !== "usage_limit") {
      gate = { kind: out.gate?.kind ?? "decision", pr_url: out.gate?.pr_url ?? null, material: out.gate?.material ?? summary };
      if (loopName) gate.loop = { name: loopName, lap: currentLapOf(loopAfter) };
    }
  } catch (err) {
    failed = true;
    summary = `ランナーが失敗した: ${(err as Error).message}`.slice(0, 2000);
    stderr(summary);
  } finally {
    (deps.clearInterval ?? clearInterval)(timer as never);
  }

  if (gate && !failed) {
    const event = truncateGate({ ...common(id, ++seq, now().toISOString()), type: "gate.reached", payload: gate } as GateReachedEvent);
    await send(event);
  }
  const finished: JobFinishedEvent = {
    ...common(id, "finished", now().toISOString()),
    type: "job.finished",
    payload: finishedPayload(report, failed, end, lapAdvanced, summary),
  } as JobFinishedEvent;
  return (await send(finished)) === "sent" ? 0 : 1;
}

// job.finished の本文。区間の上限と利用上限は stopped と理由で、ループの周回が締まった・止まる条件が書かれたジョブは
// 判断が要るでなければ done で終える（周回 1 回がスプリントの実行 1 本。決定 4・5）
export function finishedPayload(
  report: ReportResult,
  failed: boolean,
  end: SectionEnd | undefined,
  lapAdvanced: boolean,
  summary: string,
): JobFinishedEvent["payload"] {
  if (!failed && end?.kind === "usage_limit") return { result: "stopped", summary, reason: "usage_limit", resets_at: end.resetsAt };
  if (!failed && end?.kind === "section_limit") return { result: "stopped", summary, reason: "section_limit" };
  if (!failed && lapAdvanced && report !== "判断が要る") return { result: "done", summary };
  return { result: finishedResult(report, failed), summary };
}

// 環境変数から指示を読む。手元のランナー（#2189）は INGEST_TOKEN で token を直に渡す
export async function loadInstruction(env: Record<string, string | undefined>): Promise<{ instruction: RunJobInstruction; token: string }> {
  const keys = [
    "SPRINT_RUN_ID",
    "JOB_ID",
    "REPO",
    "NDF_VERSION",
    "START",
    "ISSUES",
    "APPROVAL_REQUEST_ID",
    "INGEST_URL",
    "INGEST_TOKEN_SECRET_ARN",
    "EXECUTION_MODE",
    "MODEL_CREDENTIAL_SECRET_ARN",
    "LOOP_NAME",
    "LOOP_MILESTONES",
    "LOOP_BUDGET",
    "EXCLUDE_ISSUES",
    "LOOP_ANSWERS",
  ];
  const raw = Object.fromEntries(keys.filter((k) => env[k] !== undefined).map((k) => [k, env[k]]));
  const checked = validateRunJobInstruction(raw);
  if (!checked.ok) throw new Error(`実行ジョブの指示が契約に合わない: ${checked.violation.instancePath}`);
  if (env.INGEST_TOKEN) return { instruction: checked.value, token: env.INGEST_TOKEN };
  const out = await new SecretsManagerClient({}).send(new GetSecretValueCommand({ SecretId: checked.value.INGEST_TOKEN_SECRET_ARN }));
  if (!out.SecretString) throw new Error("実行ジョブの token を読めない");
  return { instruction: checked.value, token: out.SecretString };
}

async function main(): Promise<void> {
  let code: 0 | 1 = 1;
  try {
    // SIGTERM では何も送らず 1 で終わる（実行の制御が failed を書く）
    process.on("SIGTERM", () => process.exit(1));
    const { instruction, token } = await loadInstruction(process.env);
    const { createRuntime } = await import("./runtime.ts");
    code = await runJob({ instruction, token, runtime: await createRuntime(instruction, process.env, token) });
  } catch (err) {
    process.stderr.write(`${(err as Error).message}\n`);
    code = 1;
  }
  process.exit(code);
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) await main();
