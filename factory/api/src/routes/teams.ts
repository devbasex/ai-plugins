import type { Hono } from "hono";
import { validateSeatType, validateTeamName } from "../accounts/service.ts";
import type { AuthEnv } from "../auth/resolve.ts";
import { parseIdempotencyKey } from "../idempotency.ts";
import { registerOperation } from "./operation.ts";
import { accountDetailBody } from "./views.ts";

export function registerTeams(app: Hono<AuthEnv>): void {
  // 同じ冪等の鍵の 2 回目は 1 回目のチームを返す（#2384 の I14）
  registerOperation(app, "createTeam", {
    run: async (e, _ctx, _s, c) => {
      const name = validateTeamName(e.name);
      const seatType = validateSeatType(e.seat_type);
      const key = parseIdempotencyKey(e.idempotency_key);
      const accounts = c.get("auth").accounts;
      const ctx = await accounts.createTeam(c.get("caller"), name, seatType, key);
      const account = await accounts.getAccount(ctx.accountId);
      return accountDetailBody(accounts, ctx, { name: account?.name ?? name });
    },
  });
}
