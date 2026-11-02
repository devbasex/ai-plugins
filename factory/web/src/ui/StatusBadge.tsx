export type StatusTone = "neutral" | "info" | "success" | "warning" | "danger";

// 状態の札。色だけに頼らず文言を必ず添える
export function StatusBadge({ tone, label }: { tone: StatusTone; label: string }) {
  return <span className={`ui-badge ui-badge--${tone}`}>{label}</span>;
}
