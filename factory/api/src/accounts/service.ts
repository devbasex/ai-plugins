import { randomUUID } from "node:crypto";
import { AccountUnavailableError, ConflictError, ForbiddenError, InvalidRequestError, PlanError } from "../errors.ts";
import {
  INITIAL_EXECUTION_MODE,
  isPlan,
  isSeatType,
  planFitsKind,
  type AccountContext,
  type AccountKind,
  type AccountRecord,
  type Caller,
  type ExecutionMode,
  type HistoryRecord,
  type Plan,
  type SeatRecord,
  type SeatType,
  type UserRecord,
} from "./model.ts";
import type { AccountPatch, AccountStore } from "./port.ts";
import { retryOnConflict, writeOr } from "./conflict.ts";
import { SeatService, seatHistory } from "./seat-service.ts";
import { claimOf, type IdempotencyRef } from "../idempotency.ts";

export interface ServiceDeps {
  store: AccountStore;
  now?: () => Date;
  newId?: () => string;
}

export class AccountService {
  private readonly store: AccountStore;
  private readonly now: () => Date;
  private readonly newId: () => string;
  private readonly seats: SeatService;

  constructor(deps: ServiceDeps) {
    this.store = deps.store;
    this.now = deps.now ?? (() => new Date());
    this.newId = deps.newId ?? randomUUID;
    this.seats = new SeatService(this.store, this.now);
  }

  private stamp(): string {
    return this.now().toISOString();
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
    return {
      accountId: account.accountId,
      type: "account.created",
      at: account.createdAt,
      actor,
      before: null,
      after: { kind: account.kind, plan: account.plan, execution_mode: account.executionMode, ...extra },
    };
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
    const raced = await writeOr(
      () => this.store.createUserWithPersonalAccount(user, account, [this.accountCreated(account, caller.userId)]),
      async () => {
        const found = await this.store.getUser(caller.userId);
        if (!found) throw new AccountUnavailableError();
        return found;
      },
    );
    return raced ?? user;
  }

  // 要求の利用者と経路のアカウントの ID から、権限つきの文脈を返す（決定 4、I18）
  async resolveAccount(caller: Caller, accountId?: string): Promise<AccountContext> {
    return this.resolveFor(await this.ensureUser(caller), caller, accountId);
  }

  // 読んだ利用者の行から文脈を組む。利用者の行を読み直さない
  private async resolveFor(user: UserRecord, caller: Caller, accountId?: string): Promise<AccountContext> {
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
    const personal = await this.resolveFor(user, caller);
    const out: (AccountContext & { name: string | null })[] = [{ ...personal, name: null }];
    // GSI の行は候補。外した席が GSI に残る間に名前や料金プランを返さないよう、元の席を強い整合で確かめる（I18）
    for (const accountId of await this.store.listMemberships(caller.userId)) {
      const seat = await this.store.getSeat(accountId, caller.userId);
      if (!seat) continue;
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
  // idempotencyKey があれば、同じ利用者の同じ鍵の 24 時間以内の作成を 1 件にし、2 回目は 1 回目のチームを返す（#2384 の I14）
  async createTeam(caller: Caller, name: string, seatType: SeatType, idempotencyKey?: string): Promise<AccountContext> {
    await this.ensureUser(caller);
    const at = this.stamp();
    const ref: IdempotencyRef | undefined = idempotencyKey === undefined ? undefined : { userId: caller.userId, operationId: "createTeam", key: idempotencyKey };
    const replayed = ref ? await this.store.getIdempotent(ref, at) : null;
    if (replayed) return this.resolveAccount(caller, replayed);
    const account = this.newAccount("team", "team", caller.userId, name, at);
    const seat: SeatRecord = { accountId: account.accountId, userId: caller.userId, seatType, addedAt: at };
    const won = await writeOr(
      () =>
        this.store.createTeam(
          account,
          seat,
          [
            this.accountCreated(account, caller.userId, { name }),
            seatHistory("seat.added", account.accountId, at, caller.userId, caller.userId, null, seatType),
          ],
          ref && claimOf(ref, account.accountId, at),
        ),
      async () => {
        // 同じ鍵の作成が同時に走り、先に書いた方が勝った
        const id = ref ? await this.store.getIdempotent(ref, at) : null;
        if (!id) throw new AccountUnavailableError();
        return this.resolveAccount(caller, id);
      },
    );
    return won ?? this.context(account, caller.userId, seatType);
  }

  // 席の操作は SeatService が持つ
  addSeat(ctx: AccountContext, userId: string, seatType: SeatType): Promise<SeatRecord> {
    return this.seats.addSeat(ctx, userId, seatType);
  }

  changeSeat(ctx: AccountContext, userId: string, seatType: SeatType): Promise<SeatRecord> {
    return this.seats.changeSeat(ctx, userId, seatType);
  }

  removeSeat(ctx: AccountContext, userId: string): Promise<void> {
    return this.seats.removeSeat(ctx, userId);
  }

  // 実行の形態を変える。持ち主だけ（I22）。Free で managed は断る（I21）
  async setExecutionMode(ctx: AccountContext, mode: ExecutionMode): Promise<AccountRecord> {
    if (ctx.role !== "owner") throw new ForbiddenError();
    return retryOnConflict<AccountRecord>(async () => {
      const account = await this.store.getAccount(ctx.accountId);
      if (!account) throw new ForbiddenError();
      if (account.executionMode === mode) return { done: account };
      if (mode === "managed" && account.plan === "free") {
        throw new ConflictError("managed_not_allowed_on_free", "Free の料金プランでは managed（サービスの Bedrock）を選べません");
      }
      const at = this.stamp();
      const history: HistoryRecord = {
        accountId: account.accountId,
        type: "execution_mode.changed",
        at,
        actor: ctx.userId,
        before: account.executionMode,
        after: mode,
      };
      return {
        write: () =>
          this.store.updateAccount({ accountId: account.accountId, expectedVersion: account.version, patch: { executionMode: mode }, updatedAt: at, history: [history] }),
        result: { ...account, executionMode: mode, version: account.version + 1, updatedAt: at },
      };
    });
  }

  // アカウントの料金プランを変える。#2174 が決済の後に呼ぶ内側の関数（前提 8、E6）
  // free へ下げるとき形態が managed なら、同じトランザクションで local へ変える（I21）
  async setPlan(accountId: string, plan: Plan): Promise<void> {
    if (!isPlan(plan)) throw new PlanError("unknown_plan", `知らない料金プランです: ${String(plan)}`);
    // 条件以外の書き込みの失敗は包まずにそのまま返す（呼ぶ側の #2174 が受ける）
    await retryOnConflict<void>(
      async () => {
        const account = await this.store.getAccount(accountId);
        if (!account) throw new PlanError("account_not_found", "アカウントがありません");
        if (!planFitsKind(account.kind, plan)) {
          throw new PlanError("plan_kind_mismatch", `${account.kind} のアカウントに ${plan} は置けません`);
        }
        if (account.plan === plan) return { done: undefined };
        const at = this.stamp();
        const patch: AccountPatch = { plan };
        const history: HistoryRecord[] = [{ accountId, type: "plan.changed", at, actor: "system", before: account.plan, after: plan }];
        if (plan === "free" && account.executionMode === "managed") {
          patch.executionMode = "local";
          history.push({ accountId, type: "execution_mode.changed", at, actor: "system", before: "managed", after: "local" });
        }
        return {
          write: () => this.store.updateAccount({ accountId, expectedVersion: account.version, patch, updatedAt: at, history }),
          result: undefined,
        };
      },
      (err) => err,
    );
  }
}

const TEAM_NAME_MAX_LENGTH = 64;
const TEAM_NAME_MESSAGE = `1〜${TEAM_NAME_MAX_LENGTH} 字で入力してください`;

const SEAT_TYPE_MESSAGE = "席の種類は standard か premium です";

export function validateSeatType(value: unknown): SeatType {
  if (!isSeatType(value)) throw new InvalidRequestError(SEAT_TYPE_MESSAGE);
  return value;
}

export function validateTeamName(name: unknown): string {
  if (typeof name !== "string") throw new InvalidRequestError(TEAM_NAME_MESSAGE);
  const length = [...name].length;
  if (name.trim() === "" || length > TEAM_NAME_MAX_LENGTH) throw new InvalidRequestError(TEAM_NAME_MESSAGE);
  return name;
}
