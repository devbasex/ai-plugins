// テストで使う偽の台帳。条件の確かめと書き込みを 1 つの同期の処理で行い、同時の 2 本でも 1 件にする
import type { Claim, IssueLedger, LedgerRow, Settled } from "./ledger.ts";

export class MemoryIssueLedger implements IssueLedger {
  readonly rows = new Map<string, LedgerRow>();
  readonly counts = new Map<string, number>();

  private key = (sprintRunId: string, digest: string) => `${sprintRunId}#${digest}`;

  async claim(sprintRunId: string, digest: string, requestKey: string): Promise<Claim> {
    await Promise.resolve();
    const row = this.rows.get(this.key(sprintRunId, digest));
    if (row) return { created: false, row: structuredClone(row) };
    this.rows.set(this.key(sprintRunId, digest), { state: "filing", requestKey, attemptedAt: null });
    return { created: true };
  }

  async reserve(sprintRunId: string, digest: string, limit: number, now: string): Promise<boolean> {
    await Promise.resolve();
    const count = this.counts.get(sprintRunId) ?? 0;
    const row = this.rows.get(this.key(sprintRunId, digest));
    if (row?.state !== "filing") throw new Error("依頼の行が filing でない");
    if (count >= limit) return false;
    this.counts.set(sprintRunId, count + 1);
    row.attemptedAt = now;
    return true;
  }

  async release(sprintRunId: string): Promise<void> {
    await Promise.resolve();
    const count = this.counts.get(sprintRunId) ?? 0;
    if (count > 0) this.counts.set(sprintRunId, count - 1);
  }

  async settle(sprintRunId: string, digest: string, outcome: Settled): Promise<void> {
    await Promise.resolve();
    const row = this.rows.get(this.key(sprintRunId, digest));
    if (!row) throw new Error("依頼の行が無い");
    this.rows.set(this.key(sprintRunId, digest), { requestKey: row.requestKey, ...outcome });
  }
}
