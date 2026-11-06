// 集約「オーガナイザーの進行」（#2439 の設計の「データ構造」）。根・受けた依頼・まとめたリリース・記録の行の型と、
// 状態の移りを決める純粋な関数。表への読み書きは実行の表（runs/table.ts）が持つ
import { organizerLaneName } from "@ndf-factory/contracts";
import type { ReleaseStep } from "../runs/model.ts";

export type OrganizerWaitReason = "external_release_open" | "legacy_run";

// 根（1 リポジトリに 1 行）。version は楽観的な排他の版（I67）
export interface OrganizerRoot {
  accountId: string;
  repositoryId: number;
  version: number;
  // 流れているまとめたリリースの連番。空は「流れていない」（I56）
  currentSeq: number | null;
  nextSeq: number;
  waitReason: OrganizerWaitReason | null;
  waitDetail: string | null;
  updatedAt: string;
}

export interface ReleaseAnswer {
  seq: number;
  result: "released" | "rejected";
  version: string | null;
  prodPrUrl: string | null;
  reason: string | null;
  at: string;
}

// 受けた依頼（依頼 1 件に 1 行。I59）。答えは 1 回だけ書く（I58）
export interface OrgRequest {
  accountId: string;
  repositoryId: number;
  requestId: string;
  sprintRunId: string;
  loopId: string | null;
  lane: string;
  prs: number[];
  issues: number[];
  ndfVersion: string;
  requestedAt: string;
  answer: ReleaseAnswer | null;
  // 答えを依頼元へ届け終えた時刻。空の間は見回りの索引に残る
  deliveredAt: string | null;
}

export const BATCH_STATES = ["composed", "presented", "represented", "confirmed", "stalled", "released", "rejected"] as const;
export type BatchState = (typeof BATCH_STATES)[number];
export const isTerminalBatch = (state: BatchState) => state === "released" || state === "rejected";
export type StalledFrom = "composed" | "presented" | "represented" | "confirmed";

export interface BatchLane {
  lane: string;
  sprintRunId: string;
  loopId: string | null;
  requestIds: string[];
  issues: number[];
}

export interface Presentation {
  k: 1 | 2;
  sha: string;
  version: string;
  prs: number[];
  lanes: { lane: string; requested: boolean }[];
  addedPrs: number[];
  at: string;
  // この提示へ答えた承認の依頼（承認ゲート 2）。古い承認の再送を次の提示の承認と取り違えないために残す。前の記録には無い
  approvalRequestId?: string | null;
}

export interface BatchResult {
  version: string | null;
  prodPrUrl: string | null;
  sha: string | null;
  prs: number[];
}

// まとめたリリース（I56・I57）
export interface ReleaseBatch {
  accountId: string;
  repositoryId: number;
  seq: number;
  state: BatchState;
  releaseRunId: string;
  lanes: BatchLane[];
  // 提示は上書きせず足す。2 つまで（I60）
  presentations: Presentation[];
  confirmedK: 1 | 2 | null;
  mainMerged: boolean;
  result: BatchResult | null;
  stallReason: string | null;
  stalledFrom: StalledFrom | null;
  createdAt: string;
  updatedAt: string;
}

export const LOG_KINDS = ["waited", "composed", "presented", "represented", "confirmed", "stalled", "resolved", "released", "rejected"] as const;
export type OrgLogKind = (typeof LOG_KINDS)[number];

// 記録の行（追記だけ）
export interface OrgLog {
  accountId: string;
  repositoryId: number;
  at: string;
  kind: OrgLogKind;
  seq: number | null;
  detail: Record<string, unknown>;
}

// 根の状態を変える書き込み（I67）。expectVersion が null なら根がまだ無いことを条件にする。
// batch を渡すと同じトランザクションで書き、expectBatchState があればその状態のときだけ書く
export interface OrganizerWrite {
  root: OrganizerRoot;
  expectVersion: number | null;
  batch?: ReleaseBatch;
  expectBatchState?: BatchState[];
  log?: OrgLog;
}

export const emptyRoot = (accountId: string, repositoryId: number, now: string): OrganizerRoot => ({
  accountId,
  repositoryId,
  version: 0,
  currentSeq: null,
  nextSeq: 1,
  waitReason: null,
  waitDetail: null,
  updatedAt: now,
});

// Factory の実行のレーンの名前。式は契約の organizerLaneName（ランナーの laneName と共有）
export const laneOf = (run: { sprintRunId: string }, loopName: string | null) => organizerLaneName(run.sprintRunId, loopName);

const overlaps = (a: number[], b: number[]) => a.some((n) => b.includes(n));

// 範囲に入る依頼か。Pull Request が範囲に 1 つでも入るか、Pull Request の無い依頼はまとめたリリースが受けた依頼であること
export function inRange(request: OrgRequest, prs: number[], batch: ReleaseBatch): boolean {
  if (request.prs.length === 0) return batch.lanes.some((l) => l.requestIds.includes(request.requestId));
  return overlaps(request.prs, prs);
}

// まとめたリリースの終わり方で答える依頼を決める（I61）。リリース済みは結果の prs、差し戻しは最後の提示の prs、
// 提示の前の差し戻しはまとめたリリースが受けた依頼すべて。どれにも入らない依頼は次のまとめたリリースへ残る
export function requestsToAnswer(batch: ReleaseBatch, requests: OrgRequest[], end: "released" | "rejected", settledPrs: number[] = []): OrgRequest[] {
  const open = requests.filter((r) => r.answer === null);
  if (end === "released") return open.filter((r) => inRange(r, settledPrs, batch));
  const last = batch.presentations.at(-1);
  if (!last) {
    const received = new Set(batch.lanes.flatMap((l) => l.requestIds));
    return open.filter((r) => received.has(r.requestId));
  }
  return open.filter((r) => inRange(r, last.prs, batch));
}

// 止まったリリースの選択肢（I63）。main へのマージの後は continue だけ
export const choicesOf = (batch: Pick<ReleaseBatch, "mainMerged">): ("continue" | "reject")[] => (batch.mainMerged ? ["continue"] : ["continue", "reject"]);

// 止まる前の状態（ジョブが失敗した時点の state から）。presented は提示を受けた後にジョブが落ちたとき
// （ランナーが release.presented を送った後の状態の保存に失敗したなど）で、承認ゲート 2 に届いていない
export function stalledFromOf(state: BatchState): StalledFrom | null {
  if (state === "composed" || state === "presented" || state === "represented" || state === "confirmed") return state;
  return null;
}

// 「続きから流す」の再開の手（#2439 の設計の「止まったときと再開」）。confirmed は本番のプランに手を付けた跡があれば continue
export function resumeStepOf(batch: ReleaseBatch, startedRelease: boolean): ReleaseStep {
  return stepFrom(batch.stalledFrom, batch, startedRelease);
}

// 止まる前（か今）の状態から流し直す手。判断（承認ゲートの外の問い）への答えの再開も同じ規則で決める
// presented は受けた最後の提示のプランを流し直す（提示 1 なら present、提示 2 なら represent。状態は presented のまま承認ゲート 2 を待つ）
export function stepFrom(state: StalledFrom | null, batch: Pick<ReleaseBatch, "mainMerged" | "confirmedK" | "presentations">, startedRelease: boolean): ReleaseStep {
  if (state === "composed") return "present";
  if (state === "represented") return "represent";
  if (state === "presented") return batch.presentations.at(-1)?.k === 2 ? "represent" : "present";
  if (startedRelease || batch.mainMerged) return "continue";
  return batch.confirmedK === 2 ? "confirm:2" : "confirm:1";
}

// 提示の版（本番のプランの枝 release/v<版>）。確定した提示があればその版、無ければ最後の提示の版
export function presentedVersion(batch: ReleaseBatch): string | null {
  const k = batch.confirmedK;
  const p = k ? batch.presentations.find((x) => x.k === k) : batch.presentations.at(-1);
  return p?.version ?? null;
}

// リリースの実行の NDF の版: 範囲の依頼の版のうち最も新しいもの（下限とは比べない）
export function newestVersion(versions: string[], fallback: string): string {
  const parse = (v: string) => v.split("@")[0]!.split(/[.-]/).map((x) => (/^\d+$/.test(x) ? Number(x) : -1));
  let best: string | null = null;
  for (const v of versions) {
    if (best === null) {
      best = v;
      continue;
    }
    const a = parse(v);
    const b = parse(best);
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
      const x = a[i] ?? 0;
      const y = b[i] ?? 0;
      if (x !== y) {
        if (x > y) best = v;
        break;
      }
    }
  }
  return best ?? fallback;
}
