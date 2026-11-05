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
import { ConditionFailedError, type AccountPatch, type AccountStore } from "./port.ts";
import { claimOf, type IdempotencyRef } from "../idempotency.ts";

// 版の競合で読み直して書き直す回数の上限（docs/specifications/ndf-factory-accounts-and-plans.md の「常に成り立つ条件」）
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

  // 席の履歴の行。null の側は席が無いことを表す
  private seatHistory(
    type: "seat.added" | "seat.changed" | "seat.removed",
    accountId: string,
    at: string,
    actor: string,
    userId: string,
    before: SeatType | null,
    after: SeatType | null,
  ): HistoryRecord {
    return {
      accountId,
      type,
      at,
      actor,
      before: before === null ? null : { user_id: userId, seat_type: before },
      after: after === null ? null : { user_id: userId, seat_type: after },
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
            this.seatHistory("seat.added", account.accountId, at, caller.userId, caller.userId, null, seatType),
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
    await writeOr(
      () =>
        this.store.addSeat(seat, [this.seatHistory("seat.added", ctx.accountId, at, ctx.userId, userId, null, seatType)]),
      () => {
        throw new ConflictError("seat_exists", "その利用者は既にこのチームの席を持っています");
      },
    );
    return seat;
  }

  async changeSeat(ctx: AccountContext, userId: string, seatType: SeatType): Promise<SeatRecord> {
    this.requireTeamOwner(ctx);
    // 読んだ席のままのときだけ書く。競合したら読み直し、履歴の before を作り直す
    return retryOnConflict<SeatRecord>(async () => {
      const current = await this.store.getSeat(ctx.accountId, userId);
      if (!current) throw seatNotFound();
      const at = this.stamp();
      const history = this.seatHistory("seat.changed", ctx.accountId, at, ctx.userId, userId, current.seatType, seatType);
      return { write: () => this.store.updateSeat(current, seatType, [history]), result: { ...current, seatType } };
    });
  }

  async removeSeat(ctx: AccountContext, userId: string): Promise<void> {
    this.requireTeamOwner(ctx);
    const account = await this.store.getAccount(ctx.accountId);
    if (account?.ownerUserId === userId) {
      throw new ConflictError("owner_seat_protected", "チームの持ち主の席は外せません");
    }
    // 読んだ席のままのときだけ外す。外して足し直した新しい席を古い要求で消さない
    await retryOnConflict<void>(async () => {
      const current = await this.store.getSeat(ctx.accountId, userId);
      if (!current) throw seatNotFound();
      const at = this.stamp();
      const history = this.seatHistory("seat.removed", ctx.accountId, at, ctx.userId, userId, current.seatType, null);
      return { write: () => this.store.removeSeat(current, [history]), result: undefined };
    });
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

// 読み直しと判定の 1 回分の結果。書かずに終わる（done）か、書いて終わる（write が成功したら result）
type ConflictStep<T> = { done: T } | { write: () => Promise<void>; result: T };

// 版の競合（条件の失敗）なら step から読み直して上限の回数まで書き直す。step の中で投げた誤りはそのまま届く。
// 条件以外の書き込みの失敗は onOther の結果を投げる（既定は AccountUnavailableError）
async function retryOnConflict<T>(
  step: () => Promise<ConflictStep<T>>,
  onOther: (err: unknown) => unknown = () => new AccountUnavailableError(),
): Promise<T> {
  for (let attempt = 0; attempt < VERSION_RETRIES; attempt++) {
    const next = await step();
    if ("done" in next) return next.done;
    try {
      await next.write();
      return next.result;
    } catch (err) {
      if (!(err instanceof ConditionFailedError)) throw onOther(err);
    }
  }
  throw new AccountUnavailableError();
}

// 再試行しない書き込み。成功なら undefined、条件の失敗なら onCondition の結果を返す（onCondition は投げてもよい）。
// 条件以外の失敗は AccountUnavailableError
async function writeOr<T>(write: () => Promise<void>, onCondition: () => T | Promise<T>): Promise<T | undefined> {
  try {
    await write();
    return undefined;
  } catch (err) {
    if (!(err instanceof ConditionFailedError)) throw new AccountUnavailableError();
  }
  return onCondition();
}

function seatNotFound(): NotFoundError {
  return new NotFoundError("seat_not_found", "その席はありません");
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
