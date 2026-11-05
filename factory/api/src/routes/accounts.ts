import type { Context, Hono } from "hono";
import { ForbiddenError, InvalidRequestError } from "../errors.ts";
import { isExecutionMode, type AccountContext } from "../accounts/model.ts";
import { validateSeatType } from "../accounts/service.ts";
import { resolveFromRequest, type AuthEnv } from "../auth/resolve.ts";
import { registerOperation } from "./operation.ts";
import { accountDetailBody, seatView } from "./views.ts";

function userIdOf(raw: unknown): string {
  if (typeof raw !== "string" || raw === "" || raw.length > 128) throw new InvalidRequestError("利用者の ID の形ではありません");
  return raw;
}

// アカウントそのものの操作は、要素がアカウントのため要素ごとに解決する（#2384 の決定 2）。解決できない要素は forbidden
function accountOf(c: Context<AuthEnv>, raw: unknown): Promise<AccountContext> {
  if (typeof raw !== "string" || raw === "") throw new ForbiddenError();
  return resolveFromRequest(c, raw);
}

export function registerAccounts(app: Hono<AuthEnv>): void {
  registerOperation(app, "getAccount", {
    run: async (e, _ctx, _s, c) => {
      const ctx = await accountOf(c, e.account_id);
      const accounts = c.get("auth").accounts;
      const account = await accounts.getAccount(ctx.accountId);
      if (!account) throw new ForbiddenError();
      return accountDetailBody(accounts, ctx, account);
    },
  });

  registerOperation(app, "setExecutionMode", {
    run: async (e, _ctx, _s, c) => {
      const ctx = await accountOf(c, e.account_id);
      if (!isExecutionMode(e.execution_mode)) {
        throw new InvalidRequestError("実行の形態は local・byok・managed のどれかです");
      }
      const accounts = c.get("auth").accounts;
      const account = await accounts.setExecutionMode(ctx, e.execution_mode);
      return accountDetailBody(accounts, { ...ctx, executionMode: account.executionMode, plan: account.plan }, account);
    },
  });

  registerOperation(app, "addSeat", {
    run: async (e, ctx, _s, c) => {
      const userId = userIdOf(e.user_id);
      const seatType = validateSeatType(e.seat_type);
      return seatView(await c.get("auth").accounts.addSeat(ctx!, userId, seatType));
    },
  });

  registerOperation(app, "changeSeat", {
    run: async (e, ctx, _s, c) => {
      const userId = userIdOf(e.user_id);
      const seatType = validateSeatType(e.seat_type);
      return seatView(await c.get("auth").accounts.changeSeat(ctx!, userId, seatType));
    },
  });

  registerOperation(app, "removeSeat", {
    run: async (e, ctx, _s, c) => {
      await c.get("auth").accounts.removeSeat(ctx!, userIdOf(e.user_id));
      return { removed: true };
    },
  });
}
