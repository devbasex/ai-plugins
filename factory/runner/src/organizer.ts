// Factory のオーガナイザーとの置き場の翻訳（#2439 の腐敗防止層）。NDF が置き場（NDF_ORGANIZER_DIR）へ書く依頼・提示・届いた段・結果の
// ファイルを進捗の事象へ写し、API の答え・リリース済みの範囲・まとめたリリースの中身をファイルへ書く。形は NDF の契約
// plugins/ndf/contracts/forms/parallel-external.json。API は NDF のファイルを読まない
import { mkdir, readdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import {
  organizerLaneName,
  type ProgressEvent,
  type ReleasePresentedPayload,
  type ReleaseProgressPayload,
  type ReleaseRequestedPayload,
  type ReleaseSettledPayload,
  releasePresentedKey,
  releaseProgressKey,
  releaseSettledKey,
  type RunJobInstruction,
} from "@ndf-factory/contracts";

// 置き場（取り込んだリポジトリの .ndf の下。状態の保存に入れ、周回と再開をまたいで残す）
export const ORGANIZER_DIR = ".ndf/organizer";
const STAGES = new Set(["release_branch", "develop_merged", "prod_pr_opened", "main_merged", "tagged"]);

// レーンの名前。式は契約の organizerLaneName で、API の Factory のオーガナイザーが同じ式で依頼の無いレーンを名付ける
export function laneName(inst: Pick<RunJobInstruction, "SPRINT_RUN_ID" | "LOOP_NAME">): string {
  return organizerLaneName(inst.SPRINT_RUN_ID, inst.LOOP_NAME);
}

// NDF へ渡す環境変数（I68）。Factory の実行ジョブでは 1 台の中のオーガナイザーを起動せず、リリースは依頼として出す
// NDF_VERSION は入れた NDF の版（開発版は <version>@<SHA>）。リリースの依頼の ndf_version が固定したコミットを落とさないように渡す
export function organizerEnv(inst: Pick<RunJobInstruction, "SPRINT_RUN_ID" | "LOOP_NAME" | "NDF_VERSION">, dir: string): Record<string, string> {
  return { NDF_FACTORY_RUN: "1", NDF_ORGANIZER_DIR: dir, NDF_ORGANIZER_LANE: laneName(inst), NDF_VERSION: inst.NDF_VERSION };
}

// 置き場の読み書き。テストはメモリに替える
export interface OrganizerFiles {
  write(rel: string, value: unknown): Promise<void>;
  // sub の下の .json（名前の順）。読めないものは飛ばす
  list(sub: string): Promise<{ name: string; value: Record<string, unknown> }[]>;
}

// 一時ファイルへ書いて名前を付け替える（NDF が書きかけを読まない）
export function fsOrganizerFiles(dir: string): OrganizerFiles {
  return {
    async write(rel, value) {
      const path = join(dir, rel);
      await mkdir(dirname(path), { recursive: true });
      const tmp = join(dirname(path), `.tmp-${process.pid}-${Date.now()}`);
      await writeFile(tmp, `${JSON.stringify(value, null, 2)}\n`);
      await rename(tmp, path);
    },
    async list(sub) {
      const names = (await readdir(join(dir, sub)).catch(() => [] as string[])).filter((n) => n.endsWith(".json") && !n.startsWith(".")).sort();
      const out: { name: string; value: Record<string, unknown> }[] = [];
      for (const name of names) {
        try {
          const value = JSON.parse(await readFile(join(dir, sub, name), "utf8")) as unknown;
          if (value && typeof value === "object" && !Array.isArray(value)) out.push({ name, value: value as Record<string, unknown> });
        } catch {
          // 読めないファイルは次の読みで拾う
        }
      }
      return out;
    },
  };
}

export interface OrganizerAnswers {
  answers: { request_id: string; result: string; version: string | null; release: string | null; reason: string | null; at: string }[];
  ranges: { seq: number; sha: string | null; version: string | null; prs: number[]; lanes: string[] }[];
}

// 答えと範囲を置き場へ書く（answers/<依頼の id>.json・ranges/<連番>.json）
export async function writeAnswers(files: OrganizerFiles, body: OrganizerAnswers): Promise<void> {
  for (const a of body.answers) {
    await files.write(`answers/${a.request_id}.json`, { result: a.result, version: a.version, release: a.release, reason: a.reason, at: a.at });
  }
  for (const r of body.ranges) await files.write(`ranges/${r.seq}.json`, { sha: r.sha, version: r.version, prs: r.prs, lanes: r.lanes });
}

// 写した事象（冪等の鍵つき。受け口の照合と同じ鍵）
export interface ReleaseBody {
  key: string;
  event: Pick<ProgressEvent, "type" | "payload">;
}

const ints = (v: unknown): number[] => (Array.isArray(v) ? v.filter((x): x is number => Number.isInteger(x) && x >= 1) : []);
const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);

export function requestedFrom(value: Record<string, unknown>): ReleaseRequestedPayload | null {
  const id = str(value.id);
  const lane = str(value.lane);
  const version = str(value.ndf_version);
  const at = str(value.at);
  if (!id || !lane || !version || !at || value.kind !== "request") return null;
  return { request_id: id, lane, prs: ints(value.prs), issues: ints(value.issues), ndf_version: version, requested_at: at };
}

export function presentedFrom(value: Record<string, unknown>): ReleasePresentedPayload | null {
  const sha = str(value.sha);
  const version = str(value.version);
  if (!Number.isInteger(value.seq) || (value.k !== 1 && value.k !== 2) || !sha || !version || typeof value.auto !== "boolean") return null;
  const lanes = Array.isArray(value.lanes)
    ? (value.lanes as Record<string, unknown>[]).filter((l) => typeof l?.lane === "string").map((l) => ({ lane: l.lane as string, requested: l.requested === true }))
    : [];
  return { seq: value.seq as number, k: value.k, sha, version, prs: ints(value.prs), lanes, added_prs: ints(value.added_prs), auto: value.auto };
}

export function progressFrom(value: Record<string, unknown>): ReleaseProgressPayload[] {
  if (!Number.isInteger(value.seq) || !Array.isArray(value.stages)) return [];
  return (value.stages as unknown[])
    .filter((s): s is ReleaseProgressPayload["stage"] => typeof s === "string" && STAGES.has(s))
    .map((stage) => ({ seq: value.seq as number, stage }));
}

export function settledFrom(value: Record<string, unknown>): ReleaseSettledPayload | null {
  if (!Number.isInteger(value.seq) || (value.result !== "released" && value.result !== "failed")) return null;
  const out: ReleaseSettledPayload = {
    seq: value.seq as number,
    result: value.result,
    prs: ints(value.prs),
    lanes: Array.isArray(value.lanes) ? (value.lanes as unknown[]).filter((l): l is string => typeof l === "string") : [],
  };
  const version = str(value.version);
  const url = str(value.prod_pr_url);
  const sha = str(value.sha);
  const reason = str(value.reason);
  if (version) out.version = version;
  if (url) out.prod_pr_url = url;
  if (sha) out.sha = sha;
  if (reason) out.reason = reason.slice(0, 4000);
  return out;
}

// 答えの無い依頼（再送の対象。同じ鍵で送るため受け口で 1 件になる。I59）
export async function openRequests(files: OrganizerFiles): Promise<ReleaseRequestedPayload[]> {
  const answered = new Set((await files.list("answers")).map((a) => a.name.replace(/\.json$/, "")));
  return (await files.list("requests"))
    .map((r) => requestedFrom(r.value))
    .filter((p): p is ReleaseRequestedPayload => p !== null && !answered.has(p.request_id));
}

// 置き場のファイルを進捗の事象へ写す。依頼は答えの無いものだけ、リリースの実行の事象はリリースの実行のジョブだけ（実行の識別子が鍵の頭）
export async function releaseBodies(files: OrganizerFiles, inst: Pick<RunJobInstruction, "SPRINT_RUN_ID" | "START">): Promise<ReleaseBody[]> {
  const out: ReleaseBody[] = (await openRequests(files)).map((p) => ({ key: p.request_id, event: { type: "release.requested", payload: p } }));
  if (inst.START !== "release") return out;
  const run = inst.SPRINT_RUN_ID;
  for (const f of await files.list("presented")) {
    const p = presentedFrom(f.value);
    if (p) out.push({ key: releasePresentedKey(run, p.k), event: { type: "release.presented", payload: p } });
  }
  for (const f of await files.list("progress")) {
    for (const p of progressFrom(f.value)) out.push({ key: releaseProgressKey(run, p.stage), event: { type: "release.progress", payload: p } });
  }
  for (const f of await files.list("results")) {
    const p = settledFrom(f.value);
    if (p) out.push({ key: releaseSettledKey(run, p.result), event: { type: "release.settled", payload: p } });
  }
  return out;
}

// リリースの実行の conductor への最初の指示（NDF の development-workflow の references/parallel.md の「リリースの実行」）
export const RELEASE_PROMPT = "/ndf:development-workflow --parallel-helper --external";

// 手動の実行の再開で、答えの出たリリースの依頼を conductor へ渡す指示
export function releaseAnswerDirective(requestId: string): string {
  return `リリースの依頼 ${requestId} の答えが置き場（NDF_ORGANIZER_DIR）に届いた。parallel.py await --external で読み、続きを進めてください`;
}
