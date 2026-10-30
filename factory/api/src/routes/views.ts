import { getLimits } from "../accounts/limits.ts";
import type { AccountContext, AccountRecord, SeatRecord, UserRecord } from "../accounts/model.ts";

// 応答の本文の形（factory/contracts/openapi/accounts.yaml）
export function accountSummary(ctx: AccountContext, name: string | null) {
  return {
    account_id: ctx.accountId,
    kind: ctx.kind,
    name,
    plan: ctx.plan,
    role: ctx.role,
    seat_type: ctx.seatType,
    execution_mode: ctx.executionMode,
  };
}

export function seatView(seat: SeatRecord & { idp?: UserRecord["idp"] | null }) {
  return { user_id: seat.userId, idp: seat.idp ?? null, seat_type: seat.seatType, added_at: seat.addedAt };
}

export function accountDetail(
  ctx: AccountContext,
  account: Pick<AccountRecord, "name">,
  seats: (SeatRecord & { idp: UserRecord["idp"] | null })[] | null,
) {
  const limits = getLimits(ctx);
  return {
    ...accountSummary(ctx, account.name),
    limits: limits && { concurrent_sprint_runs: limits.concurrentSprintRuns, monthly_tokens: limits.monthlyTokens },
    seats: seats && seats.map(seatView),
  };
}
