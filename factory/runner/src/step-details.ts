// 工程の中身の収集（#2172 の決定 3〜7）。NDF のプランの JSON・作業場所の git・判断の台帳（ask.jsonl）・ステップの出力（.out）から、
// step.recorded に添える項目を集める。外のファイルとコマンドに触るのはここだけで、集められなかった項目は省く（工程の送信を止めない）
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { redact } from "./redact.ts";
import { cutUtf8, phaseNameOf, phaseOf, type StepDetails } from "./translate.ts";

export const MAX_FILES = 100;
export const MAX_DECISIONS = 20;
export const MAX_DECISION_CHARS = 1000;
export const MAX_PATH_CHARS = 512;
export const MAX_OUTPUT_BYTES = 32_768;

export type GitExec = (args: string[], cwd: string) => Promise<{ code: number; stdout: string }>;

export interface StepDetailsDeps {
  git: GitExec;
  // 判断の台帳の行（kind: decision。cwd・at・question・answer を持つ）
  ledgerRows: () => Promise<Record<string, unknown>[]>;
  // 送る前に伏せる秘密の値（その時点の値）
  secrets: () => Promise<readonly string[]>;
}

export interface PlanInfo {
  // プランの JSON の "フェーズ"（読めなければ null）
  phase: string | null;
  // プランの JSON の "作業場所"（読めなければ null）
  workdir: string | null;
}

type Line = Record<string, unknown>;

// 状態のディレクトリ（<計画の名前>-state）の隣のプランの JSON（<計画の名前>.json）
export async function readPlan(stateDir: string): Promise<PlanInfo> {
  const text = await readFile(`${stateDir.replace(/-state\/?$/, "")}.json`, "utf8").catch(() => null);
  if (text === null) return { phase: null, workdir: null };
  try {
    const json = JSON.parse(text) as Record<string, unknown>;
    const str = (v: unknown) => (typeof v === "string" && v !== "" ? v : null);
    return { phase: str(json["フェーズ"]), workdir: str(json["作業場所"]) };
  } catch {
    return { phase: null, workdir: null };
  }
}

// 工程の時間の窓（at − seconds 〜 at）。at が読めなければ null
export function stepWindow(line: Line): { since: Date; until: Date } | null {
  const until = typeof line.at === "string" ? new Date(line.at) : null;
  if (!until || Number.isNaN(until.getTime())) return null;
  const seconds = typeof line.seconds === "number" && line.seconds >= 0 ? line.seconds : 0;
  return { since: new Date(until.getTime() - seconds * 1000), until };
}

// git の --since / --until は秒の精度で受ける
const gitTime = (d: Date, round: "floor" | "ceil") => {
  const ms = round === "floor" ? Math.floor(d.getTime() / 1000) * 1000 : Math.ceil(d.getTime() / 1000) * 1000;
  return `${new Date(ms).toISOString().slice(0, 19)}+00:00`;
};

const SHA = /^[0-9a-f]{40}$/;

// git log --numstat の出力から、触ったファイル（パスごとの和）と、最初の親・最後のコミットを読む
export function parseNumstat(stdout: string): Pick<StepDetails, "files" | "files_omitted" | "commit_range"> {
  const byPath = new Map<string, { added: number | null; deleted: number | null }>();
  const commits: { sha: string; parent: string | null }[] = [];
  for (const raw of stdout.split("\n")) {
    const line = raw.trimEnd();
    if (line.startsWith("@")) {
      const [sha, parent] = line.slice(1).split(" ");
      if (sha && SHA.test(sha)) commits.push({ sha, parent: parent && SHA.test(parent) ? parent : null });
      continue;
    }
    const m = /^(-|\d+)\t(-|\d+)\t(.+)$/.exec(line);
    if (!m) continue;
    const num = (v: string) => (v === "-" ? null : Number(v));
    const prev = byPath.get(m[3]!);
    const add = (a: number | null, b: number | null) => (a === null || b === null ? null : a + b);
    byPath.set(m[3]!, prev ? { added: add(prev.added, num(m[1]!)), deleted: add(prev.deleted, num(m[2]!)) } : { added: num(m[1]!), deleted: num(m[2]!) });
  }
  const all = [...byPath.entries()].map(([path, n]) => ({ path, ...n }));
  const files = all.slice(0, MAX_FILES);
  const out: Pick<StepDetails, "files" | "files_omitted" | "commit_range"> = { files, files_omitted: all.length - files.length };
  // git log は新しい順。最後のコミットが先頭、最初のコミットが末尾
  const last = commits[0];
  const first = commits.at(-1);
  if (last && first?.parent) out.commit_range = { from: first.parent, to: last.sha };
  return out;
}

const isUnder = (path: string, dir: string) => path === dir || path.startsWith(dir.endsWith("/") ? dir : `${dir}/`);

// 判断の台帳の行のうち、cwd が作業場所の下にあり、at が工程の窓に入るもの（決定 5）。文は伏せてから上限で切る
export function decisionsOf(
  rows: Line[],
  workdir: string,
  window: { since: Date; until: Date },
  clean: (text: string) => string = (t) => t,
): NonNullable<StepDetails["decisions"]> {
  const out: NonNullable<StepDetails["decisions"]> = [];
  for (const row of rows) {
    if (row.kind !== "decision" || typeof row.cwd !== "string" || !isUnder(row.cwd, workdir)) continue;
    const at = typeof row.at === "string" ? new Date(row.at).getTime() : Number.NaN;
    if (Number.isNaN(at) || at < window.since.getTime() || at > window.until.getTime()) continue;
    const text = (v: unknown) => (typeof v === "string" && v !== "" ? Array.from(clean(v)).slice(0, MAX_DECISION_CHARS).join("") : null);
    const question = text(row.question);
    const answer = text(row.answer);
    if (question && answer) out.push({ question, answer, by: "ai" });
    if (out.length >= MAX_DECISIONS) break;
  }
  return out;
}

// 1 つのステップの行の工程の中身。plan は計画の名前（5-impl-2172）、stateDir はその状態のディレクトリ
export async function collectStepDetails(deps: StepDetailsDeps, plan: string, stateDir: string, line: Line, info?: PlanInfo): Promise<StepDetails> {
  const { phase, workdir } = info ?? (await readPlan(stateDir));
  const secrets = await deps.secrets().catch(() => [] as string[]);
  const clean = (text: string) => redact(text, secrets);
  const details: StepDetails = { phase: phaseNameOf(plan, phase) };
  const issues = phaseOf(plan).issues;
  if (issues.length > 0) details.issues = issues;
  const window = stepWindow(line);
  if (workdir && window) {
    const args = ["log", "--no-merges", `--since=${gitTime(window.since, "floor")}`, `--until=${gitTime(window.until, "ceil")}`, "--numstat", "--format=@%H %P"];
    const res = await deps.git(args, workdir).catch(() => null);
    if (res && res.code === 0) {
      const parsed = parseNumstat(res.stdout);
      details.files = parsed.files!.map((f) => ({ ...f, path: Array.from(clean(f.path)).slice(0, MAX_PATH_CHARS).join("") }));
      if (parsed.files_omitted) details.files_omitted = parsed.files_omitted;
      if (parsed.commit_range) details.commit_range = parsed.commit_range;
    }
    const rows = await deps.ledgerRows().catch(() => null);
    if (rows) details.decisions = decisionsOf(rows, workdir, window, clean);
  }
  if (typeof line.out === "string" && line.out !== "") {
    const text = await readFile(join(stateDir, line.out), "utf8").catch(() => null);
    if (text !== null) {
      const cut = cutUtf8(clean(text), MAX_OUTPUT_BYTES);
      details.output = { kind: line.type === "run" ? "log" : "report", text: cut.text, truncated: cut.cut };
    }
  }
  return details;
}
