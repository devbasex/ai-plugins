// ループの記録の翻訳（腐敗防止層、決定 4・5）。NDF の .ndf/loop/<名>/loop.json を読み、loop.lap_closed・loop.stopped と
// 答えの書き込みの要否を作る。Factory はループの記録を直接読まず、ここで翻訳した事象だけを読む
import type { LoopLapClosedEvent, LoopPending, LoopStopCondition, LoopStoppedEvent } from "@ndf-factory/contracts";

type Rec = Record<string, unknown>;

const IN_PROGRESS = "進行中";
const SELECTED = "選んだ";
const RESULTS = ["完了", "失敗", "人へ戻した"] as const;
const CONDITIONS: readonly LoopStopCondition[] = [
  "user_stop",
  "mvv_changed",
  "same_failure",
  "budget_laps",
  "budget_usd",
  "budget_hours",
  "no_issue",
  "outside_mvv",
  "gate2_returned",
  "refused",
];

export interface LoopFile {
  name: string;
  laps: Rec[];
  waiting: Rec[];
  stop: Rec | null;
}

export function parseLoopFile(json: unknown): LoopFile | null {
  if (typeof json !== "object" || json === null || Array.isArray(json)) return null;
  const o = json as Rec;
  if (typeof o.name !== "string") return null;
  const arr = (v: unknown) => (Array.isArray(v) ? (v.filter((x) => typeof x === "object" && x !== null) as Rec[]) : []);
  const stop = typeof o.stop === "object" && o.stop !== null && !Array.isArray(o.stop) ? (o.stop as Rec) : null;
  return { name: o.name, laps: arr(o.laps), waiting: arr(o.waiting), stop };
}

// ジョブの始めの印。区間ごとに今の記録と比べ、周回が締まったか・止まる条件が書かれたかを決める
export interface LoopMark {
  closed: number;
  stopAt: string | null;
}

export const closedLaps = (f: LoopFile | null) => (f ? f.laps.filter((l) => l.outcome !== IN_PROGRESS) : []);

export function markOf(f: LoopFile | null): LoopMark {
  return { closed: closedLaps(f).length, stopAt: stopAtOf(f) };
}

const stopAtOf = (f: LoopFile | null) => (f?.stop ? String(f.stop.at ?? "") : null);

// 始めの印より周回が締まったか、止まる条件が新しく書かれたか
export function advancedSince(mark: LoopMark, f: LoopFile | null): boolean {
  if (closedLaps(f).length > mark.closed) return true;
  const at = stopAtOf(f);
  return at !== null && at !== mark.stopAt;
}

const int = (v: unknown) => (typeof v === "number" && Number.isInteger(v) && v >= 1 ? v : null);
const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : 0);

// 周回で選んだ課題（NDF の selected_issues と同じく、選定の行のうち「選んだ」）
function issuesOf(lap: Rec): number[] {
  const rows = Array.isArray(lap.selection) ? (lap.selection as Rec[]) : [];
  return [...new Set(rows.filter((r) => r.decision === SELECTED).map((r) => int(r.number)).filter((n): n is number => n !== null))];
}

// 人の判断待ちのうち答えの無いもの
export function pendingOf(f: LoopFile | null): LoopPending[] {
  if (!f) return [];
  const out: LoopPending[] = [];
  for (const w of f.waiting) {
    const number = int(w.number);
    const lap = int(w.lap);
    if (number === null || lap === null || w.answer) continue;
    out.push({ number, lap, reason: String(w.reason ?? "").slice(0, 4000) });
  }
  return out;
}

// 始めの印より後に締まった周回ごとに 1 件
export function lapClosedEvents(mark: LoopMark, f: LoopFile | null): Pick<LoopLapClosedEvent, "type" | "payload">[] {
  if (!f) return [];
  const pending = pendingOf(f);
  return closedLaps(f)
    .slice(mark.closed)
    .flatMap((lap) => {
      const n = int(lap.n);
      const result = RESULTS.find((r) => r === lap.outcome);
      if (n === null || !result) return [];
      const v = (typeof lap.versions === "object" && lap.versions !== null ? lap.versions : {}) as Rec;
      const version = [v.dev, v.prod].filter((x) => typeof x === "string" && x).join(" / ") || null;
      return [
        {
          type: "loop.lap_closed" as const,
          payload: {
            loop: f.name,
            lap: n,
            issues: issuesOf(lap),
            result,
            version: version ? version.slice(0, 200) : null,
            cost_usd: num(lap.cost),
            seconds: num(lap.seconds),
            pending: pending.filter((p) => p.lap === n),
          },
        },
      ];
    });
}

// 始めの印より後に書かれた止まる条件。知らない条件は送らない（実行の制御は loop.stopped の無いまま終わったものとして扱う）
export function loopStoppedEvent(mark: LoopMark, f: LoopFile | null): Pick<LoopStoppedEvent, "type" | "payload"> | null {
  if (!f?.stop || stopAtOf(f) === mark.stopAt) return null;
  const condition = CONDITIONS.find((c) => c === f.stop!.condition);
  if (!condition) return null;
  const { condition: _c, ...rest } = f.stop;
  return { type: "loop.stopped", payload: { loop: f.name, condition, detail: JSON.stringify(rest).slice(0, 4000), pending: pendingOf(f) } };
}

// LOOP_ANSWERS（<番号>=approved|rejected(#<PR>)? を , で結ぶ）を読む
export function parseAnswers(raw: string | undefined): { number: number; decision: "approved" | "rejected"; pr: number | null }[] {
  if (!raw) return [];
  return raw.split(",").flatMap((part) => {
    const m = /^([1-9][0-9]*)=(approved|rejected)(?:#([1-9][0-9]*))?$/.exec(part.trim());
    return m ? [{ number: Number(m[1]), decision: m[2] as "approved" | "rejected", pr: m[3] ? Number(m[3]) : null }] : [];
  });
}

// 答えのうち、記録の人の判断待ちにまだ答えの無い番号だけ（同じ答えを 2 回打たない）
export function answersToWrite(raw: string | undefined, f: LoopFile | null) {
  const open = new Set(pendingOf(f).map((p) => p.number));
  return parseAnswers(raw).filter((a) => open.has(a.number));
}

// 今の周回の番号（ゲートの loop の欄）。進行中の周回が無ければ締めた周回の数 + 1
export function currentLapOf(f: LoopFile | null): number {
  if (!f) return 1;
  const open = f.laps.find((l) => l.outcome === IN_PROGRESS);
  return int(open?.n) ?? closedLaps(f).length + 1;
}
