import { useTranslation } from "react-i18next";
import type { LoopBudget, LoopRow, LoopState } from "../api.ts";
import { useFormat } from "../i18n/index.ts";
import { TextField } from "../ui/TextField.tsx";
import type { StatusTone } from "../ui/StatusBadge.tsx";

const LOOP_STATE_TONES = {
  running: "info",
  between_laps: "neutral",
  awaiting_approval: "warning",
  stopped: "neutral",
  cancelled: "neutral",
} as const satisfies Record<LoopState, StatusTone>;

// 止まった理由の名前。知らない条件は値をそのまま出す
const STOP_CONDITIONS = [
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
  "run_failed",
  "run_stopped",
  "limits_exceeded",
  "launch_refused",
] as const;

type StopCondition = (typeof STOP_CONDITIONS)[number];

function isStopCondition(value: string): value is StopCondition {
  return (STOP_CONDITIONS as readonly string[]).includes(value);
}

export function useLoopView() {
  const { t } = useTranslation();
  const format = useFormat();
  const usd = (amount: number) => format.money({ amount, currency: "USD" });
  return {
    state: (state: LoopState) => ({ label: t(`loopState.${state}`), tone: LOOP_STATE_TONES[state] }),
    // 待ちの理由・止まった理由（条件の名前と detail）。どちらも無ければ空
    reason: (loop: LoopRow): string => {
      if (loop.state === "between_laps" && loop.wait_reason) return t(`loopWait.${loop.wait_reason}`);
      if (!loop.stop_condition) return "";
      const name = isStopCondition(loop.stop_condition) ? t(`loopStop.${loop.stop_condition}`) : loop.stop_condition;
      const parts = loop.stop_detail ? t("loops.reasonWithDetail", { name, detail: loop.stop_detail }) : name;
      return loop.stop_condition.startsWith("budget_") ? `${parts} ${t("loops.budgetContinue")}` : parts;
    },
    usd,
    // 値のある単位は残りを、空の単位はリポジトリの既定に従うことをまとめて 1 項目で出す（#2406）
    remaining: (b: LoopBudget): string => {
      const items = [
        typeof b.laps === "number" ? t("loops.remainingLaps", { value: format.number(b.laps) }) : null,
        typeof b.usd === "number" ? t("loops.remainingUsd", { value: usd(b.usd) }) : null,
        typeof b.hours === "number" ? t("loops.remainingHours", { value: format.number(b.hours) }) : null,
      ].filter((x): x is string => x !== null);
      const empty = (["laps", "usd", "hours"] as const).filter((k) => typeof b[k] !== "number");
      const units = { laps: t("loops.unitLaps"), usd: t("loops.unitUsd"), hours: t("loops.unitHours") };
      if (empty.length > 0) items.push(t("loops.followsDefault", { units: empty.map((k) => units[k]).join(t("loops.separator")) }));
      return items.join(" / ");
    },
  };
}

export type BudgetText = { laps: string; usd: string; hours: string };

export function budgetText(b: LoopBudget = {}): BudgetText {
  const s = (v: number | null | undefined) => (typeof v === "number" ? String(v) : "");
  return { laps: s(b.laps), usd: s(b.usd), hours: s(b.hours) };
}

// 空の欄は null（リポジトリの既定に従う）。正の数でなければ null を返して入力の誤りにする
export function parseBudget(text: BudgetText): LoopBudget | null {
  const out: LoopBudget = {};
  for (const key of ["laps", "usd", "hours"] as const) {
    const raw = text[key].trim();
    if (raw === "") {
      out[key] = null;
      continue;
    }
    const n = Number(raw);
    if (!Number.isFinite(n) || n <= 0) return null;
    out[key] = n;
  }
  return out;
}

// 予算の 3 つの欄（周回の数・費用（USD）・時間）
export function BudgetFields({ value, onChange, error }: { value: BudgetText; onChange: (v: BudgetText) => void; error: string | null }) {
  const { t } = useTranslation();
  return (
    <>
      <TextField
        label={t("loops.budgetLaps")}
        inputMode="numeric"
        placeholder={t("loops.budgetPlaceholder")}
        value={value.laps}
        onChange={(e) => onChange({ ...value, laps: e.target.value })}
      />
      <TextField
        label={t("loops.budgetUsd")}
        inputMode="decimal"
        placeholder={t("loops.budgetPlaceholder")}
        value={value.usd}
        onChange={(e) => onChange({ ...value, usd: e.target.value })}
      />
      <TextField
        label={t("loops.budgetHours")}
        inputMode="decimal"
        placeholder={t("loops.budgetPlaceholder")}
        value={value.hours}
        onChange={(e) => onChange({ ...value, hours: e.target.value })}
        error={error}
      />
    </>
  );
}

