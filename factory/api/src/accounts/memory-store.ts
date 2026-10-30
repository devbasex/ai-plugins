import { ConditionFailedError, type AccountPatch, type AccountStore } from "./store.ts";
import type { AccountRecord, HistoryRecord, SeatRecord, UserRecord } from "./model.ts";

// DynamoAccountStore と同じ条件で書く、メモリの上の保存。テストと手元の確かめに使う
// beforeWrite は書き込みの直前に呼ばれる。投げると書き込みは何も残さない（トランザクションの失敗）
export class MemoryAccountStore implements AccountStore {
  readonly users = new Map<string, UserRecord>();
  readonly accounts = new Map<string, AccountRecord>();
  readonly seats = new Map<string, SeatRecord>();
  readonly history: HistoryRecord[] = [];
  beforeWrite: ((op: string) => void | Promise<void>) | null = null;

  private static seatKey(accountId: string, userId: string) {
    return `${accountId}#${userId}`;
  }

  private async write(op: string, check: () => boolean, apply: () => void, history: HistoryRecord[]) {
    if (this.beforeWrite) await this.beforeWrite(op);
    if (!check()) throw new ConditionFailedError();
    apply();
    this.history.push(...history.map((h) => structuredClone(h)));
  }

  async getUser(userId: string) {
    const u = this.users.get(userId);
    return u ? { ...u } : null;
  }

  async createUserWithPersonalAccount(user: UserRecord, account: AccountRecord, history: HistoryRecord[]) {
    await this.write(
      "createUserWithPersonalAccount",
      () => !this.users.has(user.userId) && !this.accounts.has(account.accountId),
      () => {
        this.users.set(user.userId, { ...user });
        this.accounts.set(account.accountId, { ...account });
      },
      history,
    );
  }

  async getAccount(accountId: string) {
    const a = this.accounts.get(accountId);
    return a ? { ...a } : null;
  }

  async getSeat(accountId: string, userId: string) {
    const s = this.seats.get(MemoryAccountStore.seatKey(accountId, userId));
    return s ? { ...s } : null;
  }

  async listSeats(accountId: string) {
    return [...this.seats.values()].filter((s) => s.accountId === accountId).map((s) => ({ ...s }));
  }

  async listMemberships(userId: string) {
    return [...this.seats.values()].filter((s) => s.userId === userId).map((s) => ({ ...s }));
  }

  async createTeam(account: AccountRecord, ownerSeat: SeatRecord, history: HistoryRecord[]) {
    const key = MemoryAccountStore.seatKey(ownerSeat.accountId, ownerSeat.userId);
    await this.write(
      "createTeam",
      () => !this.accounts.has(account.accountId) && !this.seats.has(key),
      () => {
        this.accounts.set(account.accountId, { ...account });
        this.seats.set(key, { ...ownerSeat });
      },
      history,
    );
  }

  async addSeat(seat: SeatRecord, history: HistoryRecord[]) {
    const key = MemoryAccountStore.seatKey(seat.accountId, seat.userId);
    await this.write("addSeat", () => !this.seats.has(key), () => this.seats.set(key, { ...seat }), history);
  }

  private seatUnchanged(expected: SeatRecord): boolean {
    const cur = this.seats.get(MemoryAccountStore.seatKey(expected.accountId, expected.userId));
    return cur !== undefined && cur.seatType === expected.seatType && cur.addedAt === expected.addedAt;
  }

  async updateSeat(expected: SeatRecord, seatType: SeatRecord["seatType"], history: HistoryRecord[]) {
    const key = MemoryAccountStore.seatKey(expected.accountId, expected.userId);
    await this.write(
      "updateSeat",
      () => this.seatUnchanged(expected),
      () => {
        const cur = this.seats.get(key)!;
        this.seats.set(key, { ...cur, seatType });
      },
      history,
    );
  }

  async removeSeat(expected: SeatRecord, history: HistoryRecord[]) {
    const key = MemoryAccountStore.seatKey(expected.accountId, expected.userId);
    await this.write("removeSeat", () => this.seatUnchanged(expected), () => this.seats.delete(key), history);
  }

  async updateAccount(
    accountId: string,
    expectedVersion: number,
    patch: AccountPatch,
    updatedAt: string,
    history: HistoryRecord[],
  ) {
    await this.write(
      "updateAccount",
      () => this.accounts.get(accountId)?.version === expectedVersion,
      () => {
        const cur = this.accounts.get(accountId)!;
        this.accounts.set(accountId, { ...cur, ...patch, version: expectedVersion + 1, updatedAt });
      },
      history,
    );
  }
}
