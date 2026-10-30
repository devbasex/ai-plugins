import type { Hono } from "hono";
import type { AuthEnv } from "../auth/resolve.ts";
import { accountSummary } from "./views.ts";

export function registerMe(app: Hono<AuthEnv>): void {
  // 初回は個人のアカウントを作る（E2）
  app.get("/me", async (c) => {
    const { user, contexts } = await c.get("auth").accounts.listAccounts(c.get("caller"));
    return c.json({
      user_id: user.userId,
      idp: user.idp,
      personal_account_id: user.personalAccountId,
      accounts: contexts.map((ctx) => accountSummary(ctx, ctx.name)),
    });
  });
}
