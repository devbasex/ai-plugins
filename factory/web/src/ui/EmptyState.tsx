import type { ReactNode } from "react";

// 0 件のとき。次の操作があれば action に渡す
export function EmptyState({ message, action }: { message: string; action?: ReactNode }) {
  return (
    <div className="ui-state ui-state--empty">
      <p>{message}</p>
      {action}
    </div>
  );
}
