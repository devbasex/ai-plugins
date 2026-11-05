// エージェントへの委任の操作（openapi/delegations.yaml。#2384 の F2）。委任は利用者に属し、アカウントに属さない。
// 発行と取り消しは人だけが確定でき（扱い human）、委任の資格情報からは操作の登録の層が断る
import type { Context, Hono } from "hono";
import type { AuthEnv } from "../auth/resolve.ts";
import type { DelegationService } from "../delegations/service.ts";
import { ServiceUnavailableError } from "../errors.ts";
import { registerList, registerOperation } from "./operation.ts";

function service(c: Context<AuthEnv>): DelegationService {
  const d = c.get("auth").delegations;
  if (!d) throw new ServiceUnavailableError("delegation_unavailable");
  return d;
}

export function registerDelegations(app: Hono<AuthEnv>): void {
  registerList(app, "listDelegations", (c) => service(c).list(c.get("caller")));
  registerOperation(app, "createDelegation", { run: (e, _ctx, _s, c) => service(c).issue(c.get("caller"), e) });
  registerOperation(app, "revokeDelegation", { run: (e, _ctx, _s, c) => service(c).revoke(c.get("caller"), e.credential_id) });
}
