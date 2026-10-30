import { randomUUID } from "node:crypto";
import {
  AccountUnavailableError,
  ConflictError,
  ForbiddenError,
  InvalidRequestError,
  NotFoundError,
  PlanError,
  UnprocessableError,
} from "../errors.ts";
import {
  INITIAL_EXECUTION_MODE,
  isPlan,
  planFitsKind,
  type AccountContext,
  type AccountKind,
  type AccountRecord,
  type Caller,
  type ExecutionMode,
  type HistoryRecord,
  type HistoryType,
  type Plan,
  type SeatRecord,
  type SeatType,
  type UserRecord,
} from "./model.ts";
import { ConditionFailedError, type AccountPatch, type AccountStore } from "./store.ts";

// 版の競合で読み直して書き直す回数の上限（issues/issue-2170-design-interfaces.md の「処理の流れ」）
const VERSION_RETRIES = 3;

export interface ServiceDeps {
  store: AccountStore;
  now?: () => Date;
  newId?: () => string;
}

export class AccountService {
  private readonly store: AccountStore;
  private readonly now: () => Date;
  private readonly newId: () => string;

  constructor(deps: ServiceDeps) {
    this.store = deps.store;
    this.now = deps.now ?? (() => new Date());
    this.newId = deps.newId ?? randomUUID;
  }

  private stamp(): string {
    return this.now().toISOString();
  }

  private hist(accountId: string, type: HistoryType, at: string, actor: string, before: unknown, after: unknown): HistoryRecord {
    return { accountId, type, at, actor, before, after };
  }

  // 新しいアカウントの初期値（I24）。形態は INITIAL_EXECUTION_MODE、版は 1 から始める
  private newAccount(kind: AccountKind, plan: Plan, ownerUserId: string, name: string | null, at: string): AccountRecord {
    return {
      accountId: this.newId(),
      kind,
      plan,
      executionMode: INITIAL_EXECUTION_MODE,
      version: 1,
      ownerUserId,
      name,
      createdAt: at,
      updatedAt: at,
    };
  }

  private accountCreated(account: AccountRecord, actor: string, extra?: object): HistoryRecord {
    return this.hist(account.accountId, "account.created", account.createdAt, actor, null, {
      kind: account.kind,
      plan: account.plan,
      execution_mode: account.executionMode,
      ...extra,
    });
  }

  // 利用者の行を読み、無ければ利用者と個人のアカウント（Free・local）を 1 つのトランザクションで作る（I12・I24、E2）
  async ensureUser(caller: Caller): Promise<UserRecord> {
    const existing = await this.store.getUser(caller.userId);
    if (existing) return existing;
    const at = this.stamp();
    const account = this.newAccount("personal", "free", caller.userId, null, at);
    const user: UserRecord = {
      userId: caller.userId,
      idp: caller.idp,
      personalAccountId: account.accountId,
      createdAt: at,
    };
    try {
      await this.store.createUserWithPersonalAccount(user, account, [this.accountCreated(account, caller.userId)]);
      return user;
    } catch (err) {
      if (err instanceof ConditionFailedError) {
        const raced = await this.store.getUser(caller.userId);
        if (raced) return raced;
      }
      throw new AccountUnavailableError();
    }
  }

  // 要求の利用者と経路のアカウントの ID から、権限つきの文脈を返す（決定 4、I18）
  async resolveAccount(caller: Caller, accountId?: string): Promise<AccountContext> {
    const user = await this.ensureUser(caller);
    const id = accountId ?? user.personalAccountId;
    const account = await this.store.getAccount(id);
    if (!account) throw new ForbiddenError();
    if (account.kind === "personal") {
      if (account.ownerUserId !== caller.userId) throw new ForbiddenError();
      return this.context(account, caller.userId, null);
    }
    const seat = await this.store.getSeat(id, caller.userId);
    if (!seat) throw new ForbiddenError();
    return this.context(account, caller.userId, seat.seatType);
  }

  private context(account: AccountRecord, userId: string, seatType: SeatType | null): AccountContext {
    return {
      accountId: account.accountId,
      userId,
      kind: account.kind,
      plan: account.plan,
      role: account.ownerUserId === userId ? "owner" : "member",
      seatType,
      executionMode: account.executionMode,
    };
  }

  async listAccounts(caller: Caller): Promise<{ user: UserRecord; contexts: (AccountContext & { name: string | null })[] }> {
    const user = await this.ensureUser(caller);
    const personal = await this.resolveAccount(caller);
    const out: (AccountContext & { name: string | null })[] = [{ ...personal, name: null }];
    for (const seat of await this.store.listMemberships(caller.userId)) {
      const account = await this.store.getAccount(seat.accountId);
      if (!account) continue;
      out.push({ ...this.context(account, caller.userId, seat.seatType), name: account.name });
    }
    return { user, contexts: out };
  }

  async getAccount(accountId: string): Promise<AccountRecord | null> {
    return this.store.getAccount(accountId);
  }

  async listSeats(ctx: AccountContext): Promise<(SeatRecord & { idp: UserRecord["idp"] | null })[]> {
    if (ctx.kind !== "team") return [];
    const seats = await this.store.listSeats(ctx.accountId);
    return Promise.all(
      seats.map(async (s) => ({ ...s, idp: (await this.store.getUser(s.userId))?.idp ?? null })),
    );
  }

  // チームを作り、作った利用者を持ち主にして持ち主の席を 1 つ作る（受け入れ条件 9、E4）
  async createTeam(caller: Caller, name: string, seatType: SeatType): Promise<AccountContext> {
    await this.ensureUser(caller);
    const at = this.stamp();
    const account = this.newAccount("team", "team", caller.userId, name, at);
    const seat: SeatRecord = { accountId: account.accountId, userId: caller.userId, seatType, addedAt: at };
    try {
      await this.store.createTeam(account, seat, [
        this.accountCreated(account, caller.userId, { name }),
        this.hist(account.accountId, "seat.added", at, caller.userId, null, { user_id: caller.userId, seat_type: seatType }),
      ]);
    } catch {
      throw new AccountUnavailableError();
    }
    return this.context(account, caller.userId, seatType);
  }

  // 席の操作は持ち主だけ（I17）。持ち主でなければ席の有無にかかわらず先に 403
  private requireTeamOwner(ctx: AccountContext): void {
    if (ctx.role !== "owner") throw new ForbiddenError();
    if (ctx.kind !== "team") throw new ConflictError("not_a_team", "チームのアカウントではありません");
  }

  async addSeat(ctx: AccountContext, userId: string, seatType: SeatType): Promise<SeatRecord> {
    this.requireTeamOwner(ctx);
    if (!(await this.store.getUser(userId))) {
      throw new UnprocessableError("user_not_found", "その利用者はまだログインしていません");
    }
    const at = this.stamp();
    const seat: SeatRecord = { accountId: ctx.accountId, userId, seatType, addedAt: at };
    try {
      await this.store.addSeat(seat, [
        this.hist(ctx.accountId, "seat.added", at, ctx.userId, null, { user_id: userId, seat_type: seatType }),
      ]);
    } catch (err) {
      if (err instanceof ConditionFailedError) {
        throw new ConflictError("seat_exists", "その利用者は既にこのチームの席を持っています");
      }
      throw new AccountUnavailableError();
    }
    return seat;
  }

  async changeSeat(ctx: AccountContext, userId: string, seatType: SeatType): Promise<SeatRecord> {
    this.requireTeamOwner(ctx);
    const current = await this.store.getSeat(ctx.accountId, userId);
    if (!current) throw seatNotFound();
    const at = this.stamp();
    const seat: SeatRecord = { ...current, seatType };
    try {
      await this.store.updateSeat(seat, [
        this.hist(ctx.accountId, "seat.changed", at, ctx.userId, { user_id: userId, seat_type: current.seatType }, {
          user_id: userId,
          seat_type: seatType,
        }),
      ]);
    } catch (err) {
      if (err instanceof ConditionFailedError) throw seatNotFound();
      throw new AccountUnavailableError();
    }
    return seat;
  }

  async removeSeat(ctx: AccountContext, userId: string): Promise<void> {
    this.requireTeamOwner(ctx);
    const account = await this.store.getAccount(ctx.accountId);
    if (account?.ownerUserId === userId) {
      throw new ConflictError("owner_seat_protected", "チームの持ち主の席は外せません");
    }
    const current = await this.store.getSeat(ctx.accountId, userId);
    if (!current) throw seatNotFound();
    const at = this.stamp();
    try {
      await this.store.removeSeat(ctx.accountId, userId, [
        this.hist(ctx.accountId, "seat.removed", at, ctx.userId, { user_id: userId, seat_type: current.seatType }, null),
      ]);
    } catch (err) {
      if (err instanceof ConditionFailedError) throw seatNotFound();
      throw new AccountUnavailableError();
    }
  }

  // 実行の形態を変える。持ち主だけ（I22）。Free で managed は断る（I21）
  async setExecutionMode(ctx: AccountContext, mode: ExecutionMode): Promise<AccountRecord> {
    if (ctx.role !== "owner") throw new ForbiddenError();
    for (let attempt = 0; attempt < VERSION_RETRIES; attempt++) {
      const account = await this.store.getAccount(ctx.accountId);
      if (!account) throw new ForbiddenError();
      if (account.executionMode === mode) return account;
      if (mode === "managed" && account.plan === "free") {
        throw new ConflictError("managed_not_allowed_on_free", "Free のプランでは managed（サービスの Bedrock）を選べません");
      }
      const at = this.stamp();
      try {
        await this.store.updateAccount(account.accountId, account.version, { executionMode: mode }, at, [
          this.hist(account.accountId, "execution_mode.changed", at, ctx.userId, account.executionMode, mode),
        ]);
        return { ...account, executionMode: mode, version: account.version + 1, updatedAt: at };
      } catch (err) {
        if (!(err instanceof ConditionFailedError)) throw new AccountUnavailableError();
      }
    }
    throw new AccountUnavailableError();
  }

  // アカウントのプランを変える。#2174 が決済の後に呼ぶ内側の関数（前提 8、E6）
  // free へ下げるとき形態が managed なら、同じトランザクションで local へ変える（I21）
  async setPlan(accountId: string, plan: Plan): Promise<void> {
    if (!isPlan(plan)) throw new PlanError("unknown_plan", `知らないプランです: ${String(plan)}`);
    for (let attempt = 0; attempt < VERSION_RETRIES; attempt++) {
      const account = await this.store.getAccount(accountId);
      if (!account) throw new PlanError("account_not_found", "アカウントがありません");
      if (!planFitsKind(account.kind, plan)) {
        throw new PlanError("plan_kind_mismatch", `${account.kind} のアカウントに ${plan} は置けません`);
      }
      if (account.plan === plan) return;
      const at = this.stamp();
      const patch: AccountPatch = { plan };
      const history = [this.hist(accountId, "plan.changed", at, "system", account.plan, plan)];
      if (plan === "free" && account.executionMode === "managed") {
        patch.executionMode = "local";
        history.push(this.hist(accountId, "execution_mode.changed", at, "system", "managed", "local"));
      }
      try {
        await this.store.updateAccount(accountId, account.version, patch, at, history);
        return;
      } catch (err) {
        if (!(err instanceof ConditionFailedError)) throw err;
      }
    }
    throw new AccountUnavailableError();
  }
}

function seatNotFound(): NotFoundError {
  return new NotFoundError("seat_not_found", "その席はありません");
}

export function validateTeamName(name: unknown): string {
  if (typeof name !== "string") throw new InvalidRequestError("1〜64 字で入力してください");
  const length = [...name].length;
  if (name.trim() === "" || length > 64) throw new InvalidRequestError("1〜64 字で入力してください");
  return name;
}
