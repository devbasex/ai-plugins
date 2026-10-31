// ランナーの起動（F9）。実行ジョブの指示を環境変数から読み、取り込み・NDF の導入と版の固定・状態の復元・答えの記録・
// conductor の起動・翻訳と送信・状態の保存・終わりの事象を順に行う。終了コードは 0（job.finished を送った）か 1 だけ（I43）
import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";
import { validateRunJobInstruction, type GateReachedEvent, type JobFinishedEvent, type ProgressEvent, type RunJobInstruction } from "@ndf-factory/contracts";
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
  // 承認の依頼の答えを NDF へ記録する（ゲートは sprint-state.py gate --by user、判断は conductor への指示を返す）
  recordAnswer(approvalRequestId: string): Promise<string | null>;
  // conductor を動かし、伸びた分の事象を emit へ渡す。最後の作業の報告の結果と承認資料を返す
  runConductor(
    instruction: RunJobInstruction,
    directive: string | null,
    emit: (body: Pick<ProgressEvent, "type" | "payload">) => Promise<void>,
  ): Promise<{ report: ReportResult; gate: Pick<GateReachedEvent["payload"], "kind" | "pr_url" | "material"> | null; summary: string }>;
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

  const callApi = async (path: string, body: unknown = {}) => {
    const res = await http(apiUrl(instruction.INGEST_URL, path), {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`${path} が ${res.status} を返した`);
    return (await res.json()) as Record<string, string>;
  };

  const refreshGitHubToken = async () => runtime.writeGitHubToken((await callApi("/run-jobs/github-token")).token!);

  let failed = false;
  let summary = "";
  let gate: GateReachedEvent["payload"] | null = null;
  let report: ReportResult = null;
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
    if (instruction.START === "resume") {
      await runtime.restoreState((await callApi("/run-jobs/state", { op: "get" })).url!);
      directive = await runtime.recordAnswer(instruction.APPROVAL_REQUEST_ID!);
    }
    const out = await runtime.runConductor(instruction, directive, async (body) => {
      await emit(body);
    });
    report = out.report;
    summary = out.summary;
    await runtime.saveState((await callApi("/run-jobs/state", { op: "put" })).url!);
    if (report === "判断が要る") gate = { kind: out.gate?.kind ?? "decision", pr_url: out.gate?.pr_url ?? null, material: out.gate?.material ?? summary };
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
    payload: { result: finishedResult(report, failed), summary },
  } as JobFinishedEvent;
  return (await send(finished)) === "sent" ? 0 : 1;
}

// 環境変数から指示を読む。手元のランナー（#2189）は INGEST_TOKEN で token を直に渡す
export async function loadInstruction(env: Record<string, string | undefined>): Promise<{ instruction: RunJobInstruction; token: string }> {
  const keys = ["SPRINT_RUN_ID", "JOB_ID", "REPO", "NDF_VERSION", "START", "ISSUES", "APPROVAL_REQUEST_ID", "INGEST_URL", "INGEST_TOKEN_SECRET_ARN", "EXECUTION_MODE", "MODEL_CREDENTIAL_SECRET_ARN"];
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
