import { useState } from "react";
import type { AccountDetail, ExecutionMode } from "../api.ts";
import { useFailureHandler, useServices } from "../context.tsx";

export const PLAN_LABELS = { free: "Free", pro: "個人 Pro", max: "個人 Max", team: "Team" } as const;

export const MODE_LABELS: Record<ExecutionMode, string> = {
  local: "local（手元のランナー）",
  byok: "byok（キーの持ち込み）",
  managed: "managed（サービスの Bedrock）",
};

// 使える量の 2 つの上限（受け入れ条件 8）。managed でなければ月の token は掛けない
export function LimitsView({ limits }: { limits: AccountDetail["limits"] }) {
  if (!limits) return <p>使える量: 表にありません</p>;
  return (
    <dl aria-label="使える量">
      <dt>同時に動かせるスプリントの実行</dt>
      <dd>{limits.concurrent_sprint_runs}</dd>
      <dt>月の token の上限</dt>
      <dd>{limits.monthly_tokens === null ? "掛けない" : limits.monthly_tokens.toLocaleString("ja-JP")}</dd>
    </dl>
  );
}

// 実行の形態。変える操作は持ち主にだけ出す（画面の判定は表示のためだけで、認可は API が行う）
export function ExecutionModeView({ account, onChanged }: { account: AccountDetail; onChanged: (a: AccountDetail) => void }) {
  const { api } = useServices();
  const onFailure = useFailureHandler();
  const [message, setMessage] = useState<string | null>(null);
  const canChange = account.role === "owner";
  return (
    <section>
      <p>実行の形態: {MODE_LABELS[account.execution_mode]}</p>
      {canChange && (
        <label>
          実行の形態を変える
          <select
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
            {(Object.keys(MODE_LABELS) as ExecutionMode[]).map((m) => (
              <option key={m} value={m} disabled={m === "managed" && account.plan === "free"}>
                {MODE_LABELS[m]}
              </option>
            ))}
          </select>
        </label>
      )}
      {message && <p role="alert">{message}</p>}
    </section>
  );
}
