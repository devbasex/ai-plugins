import { ConflictError, ForbiddenError, NotFoundError, UnprocessableError } from "../errors.ts";
import type { AccountContext, HistoryRecord, SeatRecord, SeatType } from "./model.ts";
import type { AccountStore } from "./port.ts";
import { retryOnConflict, writeOr } from "./conflict.ts";

// 席の履歴の行。null の側は席が無いことを表す
export function seatHistory(
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

function seatNotFound(): NotFoundError {
  return new NotFoundError("seat_not_found", "その席はありません");
}

// チームの席の足し・変え・外し（I17）
export class SeatService {
  constructor(
    private readonly store: AccountStore,
    private readonly now: () => Date,
  ) {}

  private stamp(): string {
    return this.now().toISOString();
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
      () => this.store.addSeat(seat, [seatHistory("seat.added", ctx.accountId, at, ctx.userId, userId, null, seatType)]),
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
      const history = seatHistory("seat.changed", ctx.accountId, at, ctx.userId, userId, current.seatType, seatType);
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
      const history = seatHistory("seat.removed", ctx.accountId, at, ctx.userId, userId, current.seatType, null);
      return { write: () => this.store.removeSeat(current, [history]), result: undefined };
    });
  }
}
