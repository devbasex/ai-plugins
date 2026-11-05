// DynamoDelegationStore と同じ条件で書く、メモリの上の保存。テストと手元の確かめに使う
import { epochSeconds, idempotencyPk, liveCreatedId, type IdempotencyClaim, type IdempotencyRef } from "../idempotency.ts";
import type { DelegationRecord } from "./model.ts";
import type { DelegationStore } from "./store.ts";

export class MemoryDelegationStore implements DelegationStore {
  readonly records = new Map<string, DelegationRecord>();
  readonly idempotency = new Map<string, IdempotencyClaim>();
  // 読み出しの回数（取り消しを次の要求から効かせるため、照合をためないことを確かめる）
  reads = 0;

  async get(credentialId: string) {
    this.reads += 1;
    const r = this.records.get(credentialId);
    return r ? { ...r } : null;
  }

  async create(record: DelegationRecord, idempotency?: IdempotencyClaim) {
    if (this.records.has(record.credentialId)) throw new Error("condition failed");
    if (idempotency) {
      const row = this.idempotency.get(idempotencyPk(idempotency));
      if (row && row.ttl >= epochSeconds(idempotency.createdAt)) return "duplicate" as const;
      this.idempotency.set(idempotencyPk(idempotency), { ...idempotency });
    }
    this.records.set(record.credentialId, { ...record });
    return "created" as const;
  }

  async listByUser(userId: string, limit: number) {
    return [...this.records.values()]
      .filter((r) => r.userId === userId)
      .sort((a, b) => (a.createdAt < b.createdAt ? 1 : a.createdAt > b.createdAt ? -1 : 0))
      .slice(0, limit)
      .map((r) => ({ ...r }));
  }

  async revoke(credentialId: string, at: string) {
    const r = this.records.get(credentialId);
    if (r && r.revokedAt === null) r.revokedAt = at;
  }

  async getIdempotent(ref: IdempotencyRef, now: string) {
    return liveCreatedId(this.idempotency.get(idempotencyPk(ref)) ?? null, now);
  }
}
