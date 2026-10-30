import type { Hono } from "hono";
import { ForbiddenError, InvalidRequestError } from "../errors.ts";
import { isExecutionMode, isSeatType } from "../accounts/model.ts";
import { resolveFromRequest, type AuthEnv } from "../auth/resolve.ts";
import { readJson } from "./body.ts";
import { accountDetail, seatView } from "./views.ts";

const SEAT_TYPE_MESSAGE = "席の種類は standard か premium です";

export function registerAccounts(app: Hono<AuthEnv>): void {
  app.get("/accounts/:accountId", async (c) => {
    const ctx = await resolveFromRequest(c, c.req.param("accountId"));
    const accounts = c.get("auth").accounts;
    const account = await accounts.getAccount(ctx.accountId);
    if (!account) throw new ForbiddenError();
    const seats = ctx.kind === "team" ? await accounts.listSeats(ctx) : null;
    return c.json(accountDetail(ctx, account, seats));
  });

  app.put("/accounts/:accountId/execution-mode", async (c) => {
    const ctx = await resolveFromRequest(c, c.req.param("accountId"));
    const body = await readJson(c);
    if (!isExecutionMode(body.execution_mode)) {
      throw new InvalidRequestError("実行の形態は local・byok・managed のどれかです");
    }
    const accounts = c.get("auth").accounts;
    const account = await accounts.setExecutionMode(ctx, body.execution_mode);
    const next = { ...ctx, executionMode: account.executionMode, plan: account.plan };
    const seats = next.kind === "team" ? await accounts.listSeats(next) : null;
    return c.json(accountDetail(next, account, seats));
  });

  app.post("/accounts/:accountId/seats", async (c) => {
    const ctx = await resolveFromRequest(c, c.req.param("accountId"));
    const body = await readJson(c);
    if (typeof body.user_id !== "string" || body.user_id === "" || body.user_id.length > 128) {
      throw new InvalidRequestError("利用者の ID の形ではありません");
    }
    if (!isSeatType(body.seat_type)) throw new InvalidRequestError(SEAT_TYPE_MESSAGE);
    const seat = await c.get("auth").accounts.addSeat(ctx, body.user_id, body.seat_type);
    return c.json(seatView(seat), 201);
  });

  app.patch("/accounts/:accountId/seats/:userId", async (c) => {
    const ctx = await resolveFromRequest(c, c.req.param("accountId"));
    const body = await readJson(c);
    if (!isSeatType(body.seat_type)) throw new InvalidRequestError(SEAT_TYPE_MESSAGE);
    const seat = await c.get("auth").accounts.changeSeat(ctx, c.req.param("userId"), body.seat_type);
    return c.json(seatView(seat));
  });

  app.delete("/accounts/:accountId/seats/:userId", async (c) => {
    const ctx = await resolveFromRequest(c, c.req.param("accountId"));
    await c.get("auth").accounts.removeSeat(ctx, c.req.param("userId"));
    return c.body(null, 204);
  });
}
