import type { Hono } from "hono";
import { InvalidRequestError } from "../errors.ts";
import { isSeatType } from "../accounts/model.ts";
import { validateTeamName } from "../accounts/service.ts";
import type { AuthEnv } from "../auth/resolve.ts";
import { readJson } from "./body.ts";
import { accountDetail } from "./views.ts";

export function registerTeams(app: Hono<AuthEnv>): void {
  app.post("/teams", async (c) => {
    const body = await readJson(c);
    const name = validateTeamName(body.name);
    if (!isSeatType(body.seat_type)) throw new InvalidRequestError("席の種類は standard か premium です");
    const accounts = c.get("auth").accounts;
    const ctx = await accounts.createTeam(c.get("caller"), name, body.seat_type);
    const seats = await accounts.listSeats(ctx);
    return c.json(accountDetail(ctx, { name }, seats), 201);
  });
}
