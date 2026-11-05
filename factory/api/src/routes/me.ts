import type { Hono } from "hono";
import type { AuthEnv } from "../auth/resolve.ts";
import { registerList } from "./operation.ts";
import { accountSummary } from "./views.ts";

export function registerMe(app: Hono<AuthEnv>): void {
  // 初回は個人のアカウントを作る（E2）。要素 1 個の配列で返す
  registerList(app, "getMe", async (c) => {
    const { user, contexts } = await c.get("auth").accounts.listAccounts(c.get("caller"));
    return [
      {
        user_id: user.userId,
        idp: user.idp,
        personal_account_id: user.personalAccountId,
        accounts: contexts.map((ctx) => accountSummary(ctx, ctx.name)),
      },
    ];
  });
}
