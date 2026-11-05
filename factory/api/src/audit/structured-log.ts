// 操作の記録の表が無い間（インフラが AUDIT_TABLE_NAME を渡す前）の書き手。記録を構造化ログへ 1 行ずつ出し、
// 後から表へ入れ直せるようにする
import { toItem, type AuditLog, type AuditRecord } from "./log.ts";

export class StructuredLogAudit implements AuditLog {
  constructor(private readonly write: (line: string) => void = (line) => console.log(line)) {}

  async append(record: AuditRecord): Promise<void> {
    this.write(JSON.stringify({ audit: toItem(record) }));
  }
}
