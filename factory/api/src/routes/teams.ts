import type { Hono } from "hono";
import { InvalidRequestError } from "../errors.ts";
import { isSeatType } from "../accounts/model.ts";
import { validateTeamName } from "../accounts/service.ts";
import type { AuthEnv } from "../auth/resolve.ts";
import { parseIdempotencyKey } from "../idempotency.ts";
import { registerOperation } from "./operation.ts";
import { accountDetail } from "./views.ts";

export function registerTeams(app: Hono<AuthEnv>): void {
  // 同じ冪等の鍵の 2 回目は 1 回目のチームを返す（#2384 の I14）
  registerOperation(app, "createTeam", {
    run: async (e, _ctx, _s, c) => {
      const name = validateTeamName(e.name);
      if (!isSeatType(e.seat_type)) throw new InvalidRequestError("席の種類は standard か premium です");
      const key = parseIdempotencyKey(e.idempotency_key);
      const accounts = c.get("auth").accounts;
      const ctx = await accounts.createTeam(c.get("caller"), name, e.seat_type, key);
      const account = await accounts.getAccount(ctx.accountId);
      const seats = await accounts.listSeats(ctx);
      return accountDetail(ctx, { name: account?.name ?? name }, seats);
    },
  });
}
