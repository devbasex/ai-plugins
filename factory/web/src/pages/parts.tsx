import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { AccountDetail, ExecutionMode, SeatType, SprintRunSummary } from "../api.ts";
import { useFailureHandler, useServices } from "../context.tsx";
import { useFormat } from "../i18n/index.ts";
import { Alert } from "../ui/Alert.tsx";
import { SelectField } from "../ui/SelectField.tsx";
import type { StatusTone } from "../ui/StatusBadge.tsx";

export const EXECUTION_MODES: readonly ExecutionMode[] = ["local", "byok", "managed"];
export const SEAT_TYPES: readonly SeatType[] = ["standard", "premium"];

// スプリントの実行の状態と札の色の調子。知らない状態は値をそのまま出す
const SPRINT_STATE_TONES = {
  starting: "info",
  running: "info",
  awaiting_approval: "warning",
  done: "success",
  stopped: "neutral",
  failed: "danger",
  cancelled: "neutral",
} as const satisfies Record<string, StatusTone>;

type KnownState = keyof typeof SPRINT_STATE_TONES;

function isKnownState(state: string): state is KnownState {
  return Object.hasOwn(SPRINT_STATE_TONES, state);
}

export function useSprintState(): (state: string) => { label: string; tone: StatusTone } {
  const { t } = useTranslation();
  return (state) => (isKnownState(state) ? { label: t(`sprintState.${state}`), tone: SPRINT_STATE_TONES[state] } : { label: state, tone: "neutral" });
}

export function issueNumbers(run: Pick<SprintRunSummary, "issues">): string {
  return run.issues.map((n) => `#${n}`).join(" ");
}

// 使える量の 2 つの上限（受け入れ条件 8）。managed でなければ月の token は掛けない
export function LimitsView({ limits }: { limits: AccountDetail["limits"] }) {
  const { t } = useTranslation();
  const format = useFormat();
  if (!limits) return <p>{t("limits.none")}</p>;
  return (
    <dl aria-label={t("limits.label")} className="ui-details">
      <div className="ui-details__row">
        <dt>{t("limits.concurrent")}</dt>
        <dd>{limits.concurrent_sprint_runs}</dd>
      </div>
      <div className="ui-details__row">
        <dt>{t("limits.monthlyTokens")}</dt>
        <dd>{limits.monthly_tokens === null ? t("limits.unlimited") : format.number(limits.monthly_tokens)}</dd>
      </div>
    </dl>
  );
}

// 実行の形態。変える操作は持ち主にだけ出す（画面の判定は表示のためだけで、認可は API が行う）
export function ExecutionModeView({ account, onChanged }: { account: AccountDetail; onChanged: (a: AccountDetail) => void }) {
  const { t } = useTranslation();
  const { api } = useServices();
  const onFailure = useFailureHandler();
  const [message, setMessage] = useState<string | null>(null);
  const canChange = account.role === "owner";
  return (
    <section>
      <p>{t("executionMode.current", { mode: t(`mode.${account.execution_mode}`) })}</p>
      {canChange && (
        <SelectField
          label={t("executionMode.change")}
          value={account.execution_mode}
          onChange={async (e) => {
            setMessage(null);
            try {
              onChanged(await api.setExecutionMode(account.account_id, e.target.value as ExecutionMode));
            } catch (err) {
              setMessage(onFailure(err));
            }
          }}
        >
          {EXECUTION_MODES.map((m) => (
            <option key={m} value={m} disabled={m === "managed" && account.plan === "free"}>
              {t(`mode.${m}`)}
            </option>
          ))}
        </SelectField>
      )}
      {message && <Alert>{message}</Alert>}
    </section>
  );
}
