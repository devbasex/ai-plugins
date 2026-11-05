// DynamoAuditLog と同じく追記だけを行う、メモリの上の記録。テストと手元の確かめに使う
import { toItem, type AuditLog, type AuditRecord } from "./log.ts";

export class MemoryAuditLog implements AuditLog {
  readonly items: ReturnType<typeof toItem>[] = [];
  // 真なら追記に失敗する（503 の確かめ）
  fail = false;

  async append(record: AuditRecord): Promise<void> {
    if (this.fail) throw new Error("audit down");
    if (this.items.some((i) => i.record_id === record.recordId)) throw new Error("record exists");
    this.items.push(toItem(record));
  }
}
