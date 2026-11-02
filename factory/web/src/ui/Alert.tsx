import type { ReactNode } from "react";

export type AlertTone = "info" | "success" | "danger";

// 操作の結果の文。読み上げに知らせる（role="alert"）
export function Alert({ tone = "danger", children }: { tone?: AlertTone; children: ReactNode }) {
  return (
    <p role="alert" className={`ui-alert ui-alert--${tone}`}>
      {children}
    </p>
  );
}
