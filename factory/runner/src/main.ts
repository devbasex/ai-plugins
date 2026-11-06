// ランナーの起動（F9）。実行ジョブの指示を環境変数から読み、取り込み・NDF の導入と版の固定・状態の復元・答えの記録・
// conductor の起動（ndf-next の区間をつなぐ）・翻訳と送信・状態の保存・終わりの事象を順に行う。終了コードは 0（job.finished を送った）か 1 だけ（I43）
import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";
import { validateRunJobInstruction, type GateReachedEvent, type JobFinishedEvent, type ProgressEvent, type RunJobInstruction } from "@ndf-factory/contracts";
import { advancedSince, answersToWrite, currentLapOf, lapClosedEvents, loopStoppedEvent, markOf, type LoopFile } from "./loop-file.ts";
import { openRequests, releaseAnswerDirective, releaseBodies, writeAnswers, type OrganizerAnswers, type OrganizerFiles } from "./organizer.ts";
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
  // Factory のオーガナイザーとの置き場（#2439）。無ければ置き場の翻訳をしない
  organizer?: OrganizerFiles;
  // 人の判断待ちへの答えを NDF の loop answer で書く
  answerLoop(name: string, answer: { number: number; decision: "approved" | "rejected"; pr: number | null }): Promise<void>;
  // 承認の依頼の答えを NDF へ記録する（ゲートは sprint-state.py gate --by user、判断は conductor への指示を返す）
  recordAnswer(approvalRequestId: string): Promise<string | null>;
  // conductor を動かし（ndf-next の区間をつなぐ）、伸びた分の事象を emit へ渡す。最後の作業の報告の結果と承認資料、区間の終わり方を返す。
  // lapClosed はループの実行で区間ごとに呼び、真なら次の区間を起動しない（決定 5）
  runConductor(
    instruction: RunJobInstruction,
    directive: string | null,
    // at を渡すとその時刻で送る（同じ行から作る事象の時刻を揃える）
    emit: (body: Pick<ProgressEvent, "type" | "payload">, at?: string) => Promise<void>,
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
  const emit = async (body: Pick<ProgressEvent, "type" | "payload">, at?: string): Promise<SendResult> =>
    send({ ...common(id, ++seq, at ?? now().toISOString()), ...body } as ProgressEvent);

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

  const getApi = async <T>(path: string): Promise<T> => {
    const res = await http(apiUrl(instruction.INGEST_URL, path), { headers: { Authorization: `Bearer ${token}` } });
    if (!res.ok) throw new Error(`${path} が ${res.status} を返した`);
    return (await res.json()) as T;
  };
  // 置き場の事象は受け口の照合と同じ鍵で送る（送り直しでも 1 件。I59）
  const sendRelease = async (key: string, body: Pick<ProgressEvent, "type" | "payload">) =>
    send({ ...common(id, "finished", now().toISOString()), idempotency_key: key, ...body } as ProgressEvent);

  const refreshGitHubToken = async () => runtime.writeGitHubToken((await callApi("/run-jobs/github-token"))!.token!);

  let failed = false;
  let summary = "";
  let gate: GateReachedEvent["payload"] | null = null;
  let report: ReportResult = null;
  let end: SectionEnd | undefined;
  let lapAdvanced = false;
  // このジョブで新しく出たリリースの依頼があり、答えがまだ無い（手動の実行は答えを待って gate で終える。I65）
  let releaseRequested = false;
  let releaseGate = false;
  const loopName = instruction.LOOP_NAME ?? null;
  const organizer = runtime.organizer ?? null;
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
    if (instruction.START === "resume" || instruction.START === "loop" || instruction.START === "release") {
      // ループの最初の周回とリリースの実行の最初のジョブは保存した状態が無い（404）。空から始める
      const state = await callApi("/run-jobs/state", { op: "get" }, instruction.START !== "resume");
      if (state) await runtime.restoreState(state.url!);
      // 承認の依頼の識別子の無い resume（利用上限の後）は答えを書かずに続ける（決定 7）
      if (instruction.START === "resume" && instruction.APPROVAL_REQUEST_ID) directive = await runtime.recordAnswer(instruction.APPROVAL_REQUEST_ID);
      // リリースの実行の判断（承認ゲートの外の問い）への答えは本文を最初の指示へ渡す。承認ゲート 2 の答えはリリースのまとめが手（step）にする
      if (instruction.START === "release" && instruction.APPROVAL_REQUEST_ID) {
        const answer = await getApi<{ kind: string; body?: string | null }>(`/run-jobs/approval-requests/${instruction.APPROVAL_REQUEST_ID}/answer`);
        if (answer.kind === "decision") directive = answer.body ?? null;
      }
    }
    // リリースの依頼の答えと範囲、リリースの実行ならまとめたリリースの中身を置き場へ書き、答えの無い依頼を送り直す（#2439）
    let requestsBefore = new Set<string>();
    if (organizer) {
      await writeAnswers(organizer, await getApi<OrganizerAnswers>("/run-jobs/organizer/answers"));
      if (instruction.START === "release") await organizer.write("batch.json", await getApi<unknown>("/run-jobs/organizer/batch"));
      const open = await openRequests(organizer);
      requestsBefore = new Set(open.map((p) => p.request_id));
      for (const p of open) await sendRelease(p.request_id, { type: "release.requested", payload: p });
      if (instruction.RELEASE_REQUEST_ID) directive = releaseAnswerDirective(instruction.RELEASE_REQUEST_ID);
    }
    const presentedBefore = organizer ? new Set((await organizer.list("presented")).map((f) => f.name)) : new Set<string>();
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
      async (body, at) => {
        await emit(body, at);
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
    // 置き場の事象は job.finished より前に送る（実行の制御は停止の後に同じ実行の事象として読む）
    let mainMerged = false;
    if (organizer) {
      for (const body of await releaseBodies(organizer, instruction)) {
        await sendRelease(body.key, body.event);
        if (body.event.type === "release.requested" && !requestsBefore.has(body.key)) releaseRequested = true;
        if (body.event.type === "release.progress" && (body.event.payload as { stage: string }).stage === "main_merged") mainMerged = true;
      }
      releaseGate = (await organizer.list("presented")).some((f) => !presentedBefore.has(f.name) && f.value.auto === false);
    }
    await runtime.saveState((await callApi("/run-jobs/state", { op: "put" }))!.url!);
    if (report === "判断が要る" && end?.kind !== "usage_limit" && !(releaseRequested && !loopName)) {
      // リリースの実行が承認資料をそろえて止まったら承認ゲート 2。main へのマージの後の判断は続けるだけを選べる（I63）
      // リリースの実行の中の止まりは、承認資料をそろえた提示のときだけ gate2。本番のプランの中の「判断が要る」を
      // gate2 にすると、確定済みのリリースへの答えが差し戻しでも本番のプランを流し直す
      const kind = releaseGate ? "gate2" : instruction.START === "release" ? "decision" : (out.gate?.kind ?? "decision");
      gate = { kind, pr_url: out.gate?.pr_url ?? null, material: out.gate?.material ?? summary };
      if (loopName) gate.loop = { name: loopName, lap: currentLapOf(loopAfter) };
      if (instruction.START === "release" && kind === "decision" && mainMerged) gate.choices = ["continue"];
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
    payload: !failed && releaseRequested && !loopName ? { result: "gate", summary, reason: "release_requested" } : finishedPayload(report, failed, end, lapAdvanced, summary),
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

// 環境変数から指示を読む。手元のマシン（#2189）は INGEST_TOKEN で token を直に渡す
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
    "RELEASE_SEQ",
    "RELEASE_STEP",
    "RELEASE_REQUEST_ID",
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
