// ランナーの区間（決定 5・7、I51・I54）。1 つの実行ジョブの中で、conductor の最後の result の ndf-next のブロックを読み、
// 同じコンテナ・同じ作業場所で claude -p を起動し直す（手元の relay.py の役割）。打ち切りの時間はジョブの全体に掛かる
import { readNext } from "./next-block.ts";

// 区間の上限の既定（relay の記録 14 本の区間の数の最大 9 の 2 倍を超える値）
export const DEFAULT_SECTION_LIMIT = 20;

export function sectionLimitOf(env: Record<string, string | undefined>): number {
  const n = Number(env.RUNNER_SECTION_LIMIT);
  return Number.isInteger(n) && n >= 1 ? n : DEFAULT_SECTION_LIMIT;
}

// 1 回の claude -p の結果。text は最後の result の文、result はその行（無ければ null）
export interface SectionRun {
  code: number;
  text: string;
  result: Record<string, unknown> | null;
  stderr?: string;
}

export type SectionEnd =
  | { kind: "finished" } // ブロックが無い・判断が要る・ブロックが 2 つ以上（今の判定のまま）
  | { kind: "lap_closed" } // ループの周回が締まった・止まる条件が書かれた（次の周回は実行の制御が新しい実行で起こす）
  | { kind: "section_limit" }
  | { kind: "usage_limit"; resetsAt: string | null };

export interface SectionsResult {
  end: SectionEnd;
  sections: number;
  last: SectionRun;
}

export interface SectionsDeps {
  firstPrompt: string;
  limit: number;
  // 形態が local のときだけ利用上限を読む（I54）
  detectUsageLimit: boolean;
  launch: (prompt: string, section: number) => Promise<SectionRun>;
  started: (section: number, command: string) => Promise<void>;
  // 区間の後に、ループの周回が締まったか・止まる条件が書かれたか（ループの実行だけ）
  lapClosed?: () => Promise<boolean>;
  now?: () => Date;
}

export async function runSections(deps: SectionsDeps): Promise<SectionsResult> {
  let prompt = deps.firstPrompt;
  for (let section = 1; ; section += 1) {
    await deps.started(section, prompt);
    const last = await deps.launch(prompt, section);
    if (deps.detectUsageLimit) {
      const hit = usageLimitOf(last, deps.now?.() ?? new Date());
      if (hit) return { end: { kind: "usage_limit", resetsAt: hit.resetsAt }, sections: section, last };
    }
    if (last.code !== 0) return { end: { kind: "finished" }, sections: section, last };
    if (deps.lapClosed && (await deps.lapClosed())) return { end: { kind: "lap_closed" }, sections: section, last };
    const next = readNext(last.text);
    if (!next.launch) return { end: { kind: "finished" }, sections: section, last };
    if (section >= deps.limit) return { end: { kind: "section_limit" }, sections: section, last };
    prompt = next.command!;
  }
}

// NDF と同じ文言の規則（plugins/ndf/scripts/lib/claude_usage.py の LIMIT_EPOCH・LIMIT_RESETS）
const LIMIT_EPOCH = /usage limit reached\|(\d{9,11})/i;
const LIMIT_RESETS = /resets?(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)?(?:\s*\(([^)]+)\))?/i;
const LIMIT_WORDS = /usage limit|limit reached|hit your limit|rate limit/i;

// 利用上限で止まったか。result の is_error（か終了コード）と文言で読む。リセット時刻を読めなければ resetsAt は null
export function usageLimitOf(run: SectionRun, now: Date): { resetsAt: string | null } | null {
  const isError = run.result?.is_error === true || run.code !== 0;
  const text = [run.text, run.stderr ?? ""].join("\n");
  const status = run.result?.api_error_status;
  if (!isError || !(LIMIT_EPOCH.test(text) || LIMIT_WORDS.test(text) || status === 429)) return null;
  return { resetsAt: limitResetAt(text, now) };
}

export function limitResetAt(text: string, now: Date): string | null {
  const epoch = LIMIT_EPOCH.exec(text);
  if (epoch) return new Date(Number(epoch[1]) * 1000).toISOString();
  const m = LIMIT_RESETS.exec(text);
  if (!m) return null;
  let hour = Number(m[1]);
  const minute = Number(m[2] ?? 0);
  const ampm = (m[3] ?? "").toLowerCase();
  if (ampm) {
    if (hour < 1 || hour > 12) return null;
    hour = (hour % 12) + (ampm === "pm" ? 12 : 0);
  }
  if (hour > 23 || minute > 59) return null;
  const zone = m[4]?.trim() ?? "UTC";
  const offset = zoneOffsetMinutes(zone, now);
  if (offset === null) return null;
  // その時間帯の今日の hour:minute を UTC にし、今より前なら翌日にする
  const local = new Date(now.getTime() + offset * 60_000);
  let at = Date.UTC(local.getUTCFullYear(), local.getUTCMonth(), local.getUTCDate(), hour, minute) - offset * 60_000;
  if (at <= now.getTime()) at += 24 * 3600_000;
  return new Date(at).toISOString();
}

// 時間帯の UTC からのずれ（分）。読めない時間帯は null
function zoneOffsetMinutes(zone: string, at: Date): number | null {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      hourCycle: "h23",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).formatToParts(at);
    const get = (t: string) => Number(parts.find((p) => p.type === t)?.value);
    const asUtc = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour"), get("minute"));
    return Math.round((asUtc - Math.floor(at.getTime() / 60_000) * 60_000) / 60_000);
  } catch {
    return null;
  }
}
