// テストで使う偽の保存。冪等の鍵の確かめと書き込みを 1 つの同期の処理で行い、同時の 2 本でも 1 件にする
import type { ProgressEventRecord, ProgressEventStore } from "./store.ts";

export class MemoryProgressEventStore implements ProgressEventStore {
  readonly events: ProgressEventRecord[] = [];
  private readonly keys = new Set<string>();
  // テストで失敗を起こす（503 の確かめ）
  failNext: Error | null = null;

  async append(record: ProgressEventRecord): Promise<{ written: boolean }> {
    await Promise.resolve();
    if (this.failNext) {
      const err = this.failNext;
      this.failNext = null;
      throw err;
    }
    if (this.keys.has(record.idempotency_key)) return { written: false };
    this.keys.add(record.idempotency_key);
    this.events.push(structuredClone(record));
    return { written: true };
  }

  async findSprintRunsByPrRef(prRef: string): Promise<string[]> {
    return [...new Set(this.events.filter((e) => e.pr_ref === prRef).map((e) => e.sprint_run_id))];
  }

  // スプリントの実行の列（order_key の順）
  listRun(sprintRunId: string): ProgressEventRecord[] {
    return this.events
      .filter((e) => e.sprint_run_id === sprintRunId)
      .sort((a, b) => (a.order_key < b.order_key ? -1 : a.order_key > b.order_key ? 1 : 0));
  }
}
